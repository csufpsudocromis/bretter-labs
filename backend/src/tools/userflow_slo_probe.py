from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

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


def main() -> int:
    check_type = str(os.environ.get("SLO_CHECK_TYPE") or "").strip().lower()
    lookback_minutes = max(5, int(os.environ.get("SLO_LOOKBACK_MINUTES") or "30"))
    vm_launch_threshold = int(os.environ.get("VM_LAUNCH_FAILURE_RATE_PCT") or "25")
    rdp_stuck_minutes = max(2, int(os.environ.get("RDP_STUCK_MINUTES") or "12"))
    rdp_stuck_max = max(0, int(os.environ.get("RDP_STUCK_MAX") or "2"))
    upload_threshold = int(os.environ.get("UPLOAD_FINALIZE_FAILURE_RATE_PCT") or "25")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=lookback_minutes)
    rdp_stuck_cutoff = now - timedelta(minutes=rdp_stuck_minutes)

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

    print(f"FAIL: unsupported SLO_CHECK_TYPE={check_type!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
