from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin

import requests
from sqlalchemy import create_engine, text


def _database_url() -> str:
    database_url = str(os.environ.get("BLABS_DATABASE_URL") or "").strip()
    if not database_url:
        raise RuntimeError("BLABS_DATABASE_URL is not set.")
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url[len("postgres://") :]
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url[len("postgresql://") :]
    return database_url


def _pct(failed: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return (failed / total) * 100.0


def _bool_env(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _api_session(*, verify_tls: bool) -> requests.Session:
    session = requests.Session()
    session.verify = verify_tls
    session.headers.update({"Accept": "application/json"})
    return session


def _api_request_json(
    *,
    session: requests.Session,
    method: str,
    base_url: str,
    path_or_url: str,
    json_payload: dict[str, Any] | None = None,
) -> requests.Response:
    path = str(path_or_url or "").strip()
    if path.startswith("http://") or path.startswith("https://"):
        url = path
    else:
        if not path.startswith("/"):
            path = "/" + path
        url = urljoin(base_url + "/", path.lstrip("/"))
    return session.request(method=method, url=url, json=json_payload, timeout=20)


def _run_rdp_connect_latency_check(
    *,
    threshold_seconds: float,
    require_instance: bool,
    api_base: str,
    api_username: str,
    api_password: str,
    verify_tls: bool,
) -> int:
    if not api_base:
        print("FAIL: SLO_API_BASE is required for rdp_connect_latency.", file=sys.stderr)
        return 1
    if not api_username or not api_password:
        print("FAIL: SLO_API_USERNAME and SLO_API_PASSWORD are required for rdp_connect_latency.", file=sys.stderr)
        return 1

    session = _api_session(verify_tls=verify_tls)
    login = _api_request_json(
        session=session,
        method="POST",
        base_url=api_base,
        path_or_url="/auth/login",
        json_payload={"username": api_username, "password": api_password},
    )
    if login.status_code != 200:
        print(
            f"FAIL: rdp_connect_latency login failed ({login.status_code}): {login.text[:200]}",
            file=sys.stderr,
        )
        return 1

    pods = _api_request_json(
        session=session,
        method="GET",
        base_url=api_base,
        path_or_url="/user/pods",
    )
    if pods.status_code != 200:
        print(
            f"FAIL: rdp_connect_latency failed to list /user/pods ({pods.status_code}): {pods.text[:200]}",
            file=sys.stderr,
        )
        return 1

    rows = pods.json() or []
    candidate = next(
        (
            row
            for row in rows
            if str(row.get("status") or "").lower() == "running"
            and str(row.get("status_stage") or "").lower() in {"", "running", "ready"}
            and "/rdp.html" in str(row.get("console_url") or "").lower()
        ),
        None,
    )
    if candidate is None:
        message = "rdp_connect_latency: no running RDP/Guacamole VM instances found; skipping probe."
        if require_instance:
            print(f"FAIL: {message}", file=sys.stderr)
            return 1
        print(f"SKIP: {message}")
        return 0

    instance_id = str(candidate.get("id") or "").strip()
    if not instance_id:
        print("FAIL: rdp_connect_latency selected candidate without instance id.", file=sys.stderr)
        return 1

    started = time.monotonic()
    token_resp = _api_request_json(
        session=session,
        method="POST",
        base_url=api_base,
        path_or_url=f"/user/pods/{instance_id}/connect-token",
    )
    if token_resp.status_code != 200:
        print(
            f"FAIL: rdp_connect_latency connect-token failed ({token_resp.status_code}): {token_resp.text[:200]}",
            file=sys.stderr,
        )
        return 1

    token_payload = token_resp.json() or {}
    connect_url = str(token_payload.get("connect_url") or "").strip()
    if not connect_url:
        print("FAIL: rdp_connect_latency connect-token response missing connect_url.", file=sys.stderr)
        return 1
    if "/rdp.html" not in connect_url.lower():
        print(f"FAIL: rdp_connect_latency expected rdp.html connect_url (got {connect_url!r}).", file=sys.stderr)
        return 1

    connect_page = _api_request_json(
        session=session,
        method="GET",
        base_url=api_base,
        path_or_url=connect_url,
    )
    if connect_page.status_code != 200:
        print(
            f"FAIL: rdp_connect_latency connect page failed ({connect_page.status_code}): {connect_page.text[:200]}",
            file=sys.stderr,
        )
        return 1
    body = connect_page.text or ""
    if "RDP Console" not in body or "guacamole/all.min.js" not in body or 'id="display"' not in body:
        print("FAIL: rdp_connect_latency connect page did not include expected Guacamole RDP markers.", file=sys.stderr)
        return 1

    elapsed = time.monotonic() - started
    print(
        f"rdp_connect_latency_slo: instance_id={instance_id} latency_seconds={elapsed:.2f} "
        f"threshold_seconds={threshold_seconds:.2f}"
    )
    if elapsed > threshold_seconds:
        print(
            f"FAIL: rdp connect latency breached threshold ({elapsed:.2f}s > {threshold_seconds:.2f}s).",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    check_type = str(os.environ.get("SLO_CHECK_TYPE") or "").strip().lower()
    lookback_minutes = max(5, int(os.environ.get("SLO_LOOKBACK_MINUTES") or "30"))
    vm_launch_threshold = int(os.environ.get("VM_LAUNCH_FAILURE_RATE_PCT") or "25")
    rdp_stuck_minutes = max(2, int(os.environ.get("RDP_STUCK_MINUTES") or "12"))
    rdp_stuck_max = max(0, int(os.environ.get("RDP_STUCK_MAX") or "2"))
    upload_threshold = int(os.environ.get("UPLOAD_FINALIZE_FAILURE_RATE_PCT") or "25")
    image_import_queue_age_threshold_minutes = max(
        5,
        int(os.environ.get("IMAGE_IMPORT_QUEUE_MAX_AGE_MINUTES") or "30"),
    )
    rdp_connect_latency_threshold_seconds = max(
        1.0,
        float(os.environ.get("RDP_CONNECT_LATENCY_SECONDS") or "20"),
    )
    require_rdp_instance = _bool_env("RDP_CONNECT_REQUIRE_INSTANCE", False)

    api_base = str(os.environ.get("SLO_API_BASE") or "").strip().rstrip("/")
    api_username = str(os.environ.get("SLO_API_USERNAME") or "").strip()
    api_password = str(os.environ.get("SLO_API_PASSWORD") or "").strip()
    api_verify_tls = _bool_env("SLO_API_VERIFY_TLS", True)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=lookback_minutes)
    rdp_stuck_cutoff = now - timedelta(minutes=rdp_stuck_minutes)

    if check_type == "rdp_connect_latency":
        return _run_rdp_connect_latency_check(
            threshold_seconds=rdp_connect_latency_threshold_seconds,
            require_instance=require_rdp_instance,
            api_base=api_base,
            api_username=api_username,
            api_password=api_password,
            verify_tls=api_verify_tls,
        )

    engine = create_engine(_database_url(), pool_pre_ping=True)
    with engine.connect() as conn:
        if check_type == "vm_launch":
            total = int(
                conn.execute(
                    text("SELECT COUNT(*) FROM instance WHERE started_at >= :cutoff"),
                    {"cutoff": cutoff},
                ).scalar_one()
                or 0
            )
            failed = int(
                conn.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM instance
                        WHERE started_at >= :cutoff
                        AND lower(COALESCE(status, '')) IN ('failed', 'error')
                        """
                    ),
                    {"cutoff": cutoff},
                ).scalar_one()
                or 0
            )
            failure_pct = _pct(failed, total)
            print(
                f"vm_launch_slo: total={total} failed={failed} failure_rate_pct={failure_pct:.2f} "
                f"threshold_pct={vm_launch_threshold}"
            )
            if total >= 3 and failure_pct > float(vm_launch_threshold):
                print("FAIL: vm launch failure rate breached threshold.", file=sys.stderr)
                return 1
            return 0

        if check_type == "rdp_readiness":
            totals = (
                conn.execute(
                    text(
                        """
                    SELECT
                      COUNT(*) AS total,
                      SUM(CASE WHEN lower(COALESCE(i.status, '')) = 'running' THEN 1 ELSE 0 END) AS running,
                      SUM(CASE WHEN lower(COALESCE(i.status, '')) IN ('pending', 'building', 'starting') THEN 1 ELSE 0 END) AS pending,
                      SUM(
                        CASE
                          WHEN i.started_at <= :stuck_cutoff
                           AND lower(COALESCE(i.status, '')) IN ('pending', 'building', 'starting')
                          THEN 1 ELSE 0
                        END
                      ) AS stuck
                    FROM instance i
                    JOIN template t ON t.id = i.template_id
                    WHERE lower(COALESCE(t.console_provider, '')) IN
                      ('guacamole_rdp', 'guacamole-rdp', 'guac-rdp', 'rdp')
                    """
                    ),
                    {"stuck_cutoff": rdp_stuck_cutoff},
                )
                .mappings()
                .one()
            )
            total = int(totals.get("total") or 0)
            running = int(totals.get("running") or 0)
            pending = int(totals.get("pending") or 0)
            stuck = int(totals.get("stuck") or 0)

            oldest_stuck = (
                conn.execute(
                    text(
                        """
                    SELECT i.id, i.owner, i.started_at
                    FROM instance i
                    JOIN template t ON t.id = i.template_id
                    WHERE i.started_at <= :stuck_cutoff
                    AND lower(COALESCE(i.status, '')) IN ('pending', 'building', 'starting')
                    AND lower(COALESCE(t.console_provider, '')) IN
                      ('guacamole_rdp', 'guacamole-rdp', 'guac-rdp', 'rdp')
                    ORDER BY i.started_at ASC
                    LIMIT 3
                    """
                    ),
                    {"stuck_cutoff": rdp_stuck_cutoff},
                )
                .mappings()
                .all()
            )
            sample = ",".join(f"{str(row['owner'])}/{str(row['id'])[:8]}" for row in oldest_stuck)
            print(
                "rdp_readiness_slo: "
                f"total_rdp_instances={total} running={running} pending_or_starting={pending} "
                f"stuck_instances={stuck} stuck_minutes={rdp_stuck_minutes} max_allowed={rdp_stuck_max}"
            )
            if sample:
                print(f"rdp_readiness_oldest_stuck: {sample}")
            if stuck > rdp_stuck_max:
                print("FAIL: rdp readiness stuck-instance threshold breached.", file=sys.stderr)
                return 1
            return 0

        if check_type == "upload_finalize":
            total = int(
                conn.execute(
                    text("SELECT COUNT(*) FROM imageuploadtask WHERE created_at >= :cutoff"),
                    {"cutoff": cutoff},
                ).scalar_one()
                or 0
            )
            failed = int(
                conn.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM imageuploadtask
                        WHERE created_at >= :cutoff
                        AND (
                          lower(COALESCE(status, '')) = 'failed'
                          OR lower(COALESCE(stage, '')) = 'failed'
                        )
                        """
                    ),
                    {"cutoff": cutoff},
                ).scalar_one()
                or 0
            )
            failure_pct = _pct(failed, total)
            print(
                f"upload_finalize_slo: total={total} failed={failed} failure_rate_pct={failure_pct:.2f} "
                f"threshold_pct={upload_threshold}"
            )
            if total >= 3 and failure_pct > float(upload_threshold):
                print("FAIL: upload finalize failure rate breached threshold.", file=sys.stderr)
                return 1
            return 0

        if check_type == "image_import_queue_age":
            row = (
                conn.execute(
                    text(
                        """
                        SELECT
                          COUNT(*) AS queued_count,
                          COALESCE(
                            MAX(EXTRACT(EPOCH FROM (NOW() - created_at)) / 60.0),
                            0
                          ) AS oldest_age_minutes
                        FROM imageuploadtask
                        WHERE lower(COALESCE(status, '')) NOT IN ('completed', 'failed')
                          AND lower(COALESCE(stage, '')) NOT IN ('completed', 'failed')
                        """
                    )
                )
                .mappings()
                .one()
            )
            queued_count = int(row.get("queued_count") or 0)
            oldest_age_minutes = float(row.get("oldest_age_minutes") or 0.0)
            print(
                "image_import_queue_age_slo: "
                f"queued={queued_count} oldest_age_minutes={oldest_age_minutes:.2f} "
                f"threshold_minutes={image_import_queue_age_threshold_minutes}"
            )
            if queued_count > 0 and oldest_age_minutes > float(image_import_queue_age_threshold_minutes):
                print(
                    "FAIL: image import queue age breached threshold "
                    f"({oldest_age_minutes:.2f}m > {image_import_queue_age_threshold_minutes}m).",
                    file=sys.stderr,
                )
                return 1
            return 0

    print(f"FAIL: unsupported SLO_CHECK_TYPE={check_type!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
