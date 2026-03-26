#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any
from urllib.parse import urljoin

import requests


def _bool_env(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _request(
    session: requests.Session,
    *,
    method: str,
    api_base: str,
    path_or_url: str,
    verify_tls: bool,
    json_payload: dict[str, Any] | None = None,
) -> requests.Response:
    path = str(path_or_url or "").strip()
    if path.startswith("http://") or path.startswith("https://"):
        url = path
    else:
        if not path.startswith("/"):
            path = "/" + path
        url = urljoin(api_base + "/", path.lstrip("/"))
    return session.request(method=method, url=url, json=json_payload, timeout=20, verify=verify_tls)


def _fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def _wait_for(
    *,
    session: requests.Session,
    api_base: str,
    verify_tls: bool,
    list_path: str,
    instance_id: str,
    deadline_epoch: float,
    poll_seconds: float,
    allowed_stages: set[str],
) -> dict[str, Any]:
    while time.time() < deadline_epoch:
        resp = _request(session, method="GET", api_base=api_base, path_or_url=list_path, verify_tls=verify_tls)
        if resp.status_code == 200:
            rows = resp.json() or []
            for row in rows:
                if str(row.get("id") or "") != instance_id:
                    continue
                stage = str(row.get("status_stage") or row.get("status") or "").strip().lower()
                if stage in allowed_stages:
                    return row
        time.sleep(poll_seconds)
    _fail(f"timeout waiting for instance {instance_id} in {list_path}")
    return {}


def _wait_deleted_or_released(
    *,
    session: requests.Session,
    api_base: str,
    verify_tls: bool,
    list_path: str,
    instance_id: str,
    deadline_epoch: float,
    poll_seconds: float,
) -> None:
    terminal = {"stopped", "completed", "failed"}
    while time.time() < deadline_epoch:
        resp = _request(session, method="GET", api_base=api_base, path_or_url=list_path, verify_tls=verify_tls)
        if resp.status_code != 200:
            time.sleep(poll_seconds)
            continue
        row = next((item for item in (resp.json() or []) if str(item.get("id") or "") == instance_id), None)
        if row is None:
            return
        status = str(row.get("status") or "").strip().lower()
        if status in terminal:
            return
        time.sleep(poll_seconds)
    _fail(f"timeout waiting for instance release: {instance_id}")


def _wait_for_rdp_connect(
    *,
    session: requests.Session,
    api_base: str,
    verify_tls: bool,
    vm_id: str,
    deadline_epoch: float,
    poll_seconds: float,
) -> str:
    while time.time() < deadline_epoch:
        token = _request(
            session,
            method="POST",
            api_base=api_base,
            path_or_url=f"/user/pods/{vm_id}/connect-token",
            verify_tls=verify_tls,
        )
        if token.status_code == 200:
            payload = token.json() or {}
            connect_url = str(payload.get("connect_url") or "").strip()
            if connect_url:
                return connect_url
            _fail("RDP connect-token response missing connect_url")
        if token.status_code == 409:
            time.sleep(poll_seconds)
            continue
        _fail(f"RDP connect-token failed ({token.status_code}): {token.text[:300]}")
    _fail("timeout waiting for RDP connect-token readiness")
    return ""


def _wait_for_container_ws_readiness(
    *,
    session: requests.Session,
    api_base: str,
    verify_tls: bool,
    container_id: str,
    deadline_epoch: float,
    poll_seconds: float,
) -> None:
    last_detail = ""
    while time.time() < deadline_epoch:
        readiness = _request(
            session,
            method="GET",
            api_base=api_base,
            path_or_url=f"/user/containers/{container_id}/connect-readiness",
            verify_tls=verify_tls,
        )
        if readiness.status_code == 200:
            payload = readiness.json() or {}
            if bool(payload.get("ready")):
                return
            last_detail = str(payload.get("detail") or "").strip()
        else:
            last_detail = f"connect-readiness returned {readiness.status_code}"
        time.sleep(poll_seconds)
    detail = last_detail[:300] if last_detail else "no readiness detail"
    _fail(f"timeout waiting for container websocket readiness: {detail}")


def _require_rdp_frame(
    *,
    session: requests.Session,
    api_base: str,
    verify_tls: bool,
    connect_url: str,
    deadline_epoch: float,
    poll_seconds: float,
) -> None:
    if "/rdp.html" not in connect_url.lower():
        _fail(f"expected /rdp.html in connect_url but found {connect_url!r}")

    while time.time() < deadline_epoch:
        page = _request(session, method="GET", api_base=api_base, path_or_url=connect_url, verify_tls=verify_tls)
        if page.status_code == 200:
            body = page.text or ""
            if 'id="display"' in body and "guacamole/all.min.js" in body:
                return
        time.sleep(poll_seconds)
    _fail("timed out waiting for Guacamole RDP frame markers.")


def main() -> int:
    api_base = str(os.environ.get("SYNTHETIC_API_BASE") or "").strip().rstrip("/")
    username = str(os.environ.get("SYNTHETIC_USERNAME") or "").strip()
    password = str(os.environ.get("SYNTHETIC_PASSWORD") or "").strip()
    verify_tls = _bool_env("SYNTHETIC_VERIFY_TLS", True)
    require_templates = _bool_env("SYNTHETIC_REQUIRE_TEMPLATES", True)
    require_rdp_template = _bool_env("SYNTHETIC_REQUIRE_RDP_TEMPLATE", False)
    timeout_seconds = max(120, int(os.environ.get("SYNTHETIC_TIMEOUT_SECONDS") or "900"))
    poll_seconds = max(1.0, float(os.environ.get("SYNTHETIC_POLL_SECONDS") or "3"))
    rdp_marker_timeout_seconds = max(30, int(os.environ.get("SYNTHETIC_RDP_MARKER_TIMEOUT_SECONDS") or "180"))
    container_ws_timeout_seconds = max(30, int(os.environ.get("SYNTHETIC_CONTAINER_WS_TIMEOUT_SECONDS") or "180"))
    single_lab_limit_message = "you already have a virtual lab running"

    if not api_base:
        _fail("SYNTHETIC_API_BASE is required.")
    if not username or not password:
        _fail("SYNTHETIC_USERNAME and SYNTHETIC_PASSWORD are required.")

    session = requests.Session()
    session.headers.update({"Accept": "application/json"})
    deadline = time.time() + timeout_seconds
    vm_id = ""
    container_id = ""

    try:
        health = _request(session, method="GET", api_base=api_base, path_or_url="/health", verify_tls=verify_tls)
        if health.status_code != 200:
            _fail(f"health check failed ({health.status_code}): {health.text[:300]}")
        if "application/json" not in str(health.headers.get("content-type") or ""):
            _fail("health endpoint did not return JSON")
        payload = health.json() or {}
        if str(payload.get("status") or "").lower() != "ok":
            _fail(f"unexpected health payload: {json.dumps(payload)[:300]}")

        login = _request(
            session,
            method="POST",
            api_base=api_base,
            path_or_url="/auth/login",
            verify_tls=verify_tls,
            json_payload={"username": username, "password": password},
        )
        if login.status_code != 200:
            _fail(f"login failed ({login.status_code}): {login.text[:300]}")

        vm_templates_resp = _request(
            session, method="GET", api_base=api_base, path_or_url="/user/templates", verify_tls=verify_tls
        )
        if vm_templates_resp.status_code != 200:
            _fail(f"failed to fetch VM templates ({vm_templates_resp.status_code})")
        vm_templates = vm_templates_resp.json() or []

        container_templates_resp = _request(
            session,
            method="GET",
            api_base=api_base,
            path_or_url="/user/container-templates",
            verify_tls=verify_tls,
        )
        if container_templates_resp.status_code != 200:
            _fail(f"failed to fetch container templates ({container_templates_resp.status_code})")
        container_templates = container_templates_resp.json() or []

        if not vm_templates or not container_templates:
            message = (
                "missing enabled VM or container templates "
                f"(vm={len(vm_templates)} container={len(container_templates)})"
            )
            if require_templates:
                _fail(message)
            print(f"SKIP: synthetic check skipped: {message}")
            return 0

        rdp_template = next(
            (row for row in vm_templates if str(row.get("console_provider") or "").strip().lower() == "guacamole_rdp"),
            None,
        )
        if rdp_template is None and require_rdp_template:
            _fail("SYNTHETIC_REQUIRE_RDP_TEMPLATE=1 but no guacamole_rdp VM template is enabled.")
        vm_template = rdp_template or vm_templates[0]
        vm_template_id = str(vm_template.get("id") or "").strip()
        container_template_id = str((container_templates[0] or {}).get("id") or "").strip()
        if not vm_template_id or not container_template_id:
            _fail("template list returned missing ids")

        vm_start = _request(
            session,
            method="POST",
            api_base=api_base,
            path_or_url=f"/user/templates/{vm_template_id}/start",
            verify_tls=verify_tls,
        )
        if vm_start.status_code != 201:
            _fail(f"VM start failed ({vm_start.status_code}): {vm_start.text[:300]}")
        vm_id = str((vm_start.json() or {}).get("id") or "").strip()
        if not vm_id:
            _fail("VM start response missing instance id")

        vm_row = _wait_for(
            session=session,
            api_base=api_base,
            verify_tls=verify_tls,
            list_path="/user/pods",
            instance_id=vm_id,
            deadline_epoch=deadline,
            poll_seconds=poll_seconds,
            allowed_stages={"pending", "building", "queued", "starting", "running", "ready"},
        )
        if not str(vm_row.get("console_url") or "").strip():
            _fail("VM instance did not publish console_url")

        if str(vm_template.get("console_provider") or "").strip().lower() == "guacamole_rdp":
            connect_url = _wait_for_rdp_connect(
                session=session,
                api_base=api_base,
                verify_tls=verify_tls,
                vm_id=vm_id,
                deadline_epoch=deadline,
                poll_seconds=poll_seconds,
            )
            _require_rdp_frame(
                session=session,
                api_base=api_base,
                verify_tls=verify_tls,
                connect_url=connect_url,
                deadline_epoch=time.time() + rdp_marker_timeout_seconds,
                poll_seconds=poll_seconds,
            )

        vm_delete = _request(
            session, method="DELETE", api_base=api_base, path_or_url=f"/user/pods/{vm_id}", verify_tls=verify_tls
        )
        if vm_delete.status_code not in {204, 404}:
            _fail(f"VM delete failed ({vm_delete.status_code}): {vm_delete.text[:300]}")
        _wait_deleted_or_released(
            session=session,
            api_base=api_base,
            verify_tls=verify_tls,
            list_path="/user/pods",
            instance_id=vm_id,
            deadline_epoch=deadline,
            poll_seconds=poll_seconds,
        )
        vm_id = ""

        while time.time() < deadline:
            container_start = _request(
                session,
                method="POST",
                api_base=api_base,
                path_or_url=f"/user/container-templates/{container_template_id}/start",
                verify_tls=verify_tls,
            )
            if container_start.status_code == 201:
                container_id = str((container_start.json() or {}).get("id") or "").strip()
                break
            detail = ""
            try:
                detail = str((container_start.json() or {}).get("detail") or "")
            except Exception:
                detail = container_start.text or ""
            if container_start.status_code == 429 and single_lab_limit_message in detail.lower():
                time.sleep(poll_seconds)
                continue
            _fail(f"container start failed ({container_start.status_code}): {detail[:300]}")
        if not container_id:
            _fail("timeout waiting for available slot for container start")

        _wait_for(
            session=session,
            api_base=api_base,
            verify_tls=verify_tls,
            list_path="/user/containers",
            instance_id=container_id,
            deadline_epoch=deadline,
            poll_seconds=poll_seconds,
            allowed_stages={"pending", "building", "queued", "starting", "running", "ready"},
        )
        _wait_for_container_ws_readiness(
            session=session,
            api_base=api_base,
            verify_tls=verify_tls,
            container_id=container_id,
            deadline_epoch=min(deadline, time.time() + container_ws_timeout_seconds),
            poll_seconds=poll_seconds,
        )

        container_token = _request(
            session,
            method="POST",
            api_base=api_base,
            path_or_url=f"/user/containers/{container_id}/connect-token",
            verify_tls=verify_tls,
        )
        if container_token.status_code != 200:
            _fail(f"container connect-token failed ({container_token.status_code}): {container_token.text[:300]}")
        connect_url = str((container_token.json() or {}).get("connect_url") or "").strip()
        if not connect_url:
            _fail("container connect-token response missing connect_url")

        idle_bridge = _request(
            session,
            method="GET",
            api_base=api_base,
            path_or_url=f"/user/containers/{container_id}/connect/__blabs_idle_bridge.js",
            verify_tls=verify_tls,
        )
        if idle_bridge.status_code != 200:
            _fail(f"container idle bridge fetch failed ({idle_bridge.status_code}): {idle_bridge.text[:300]}")
        if "Still using this lab?" not in str(idle_bridge.text or ""):
            _fail("container idle bridge did not include expected prompt content")

        container_delete = _request(
            session,
            method="DELETE",
            api_base=api_base,
            path_or_url=f"/user/containers/{container_id}",
            verify_tls=verify_tls,
        )
        if container_delete.status_code not in {204, 404}:
            _fail(f"container delete failed ({container_delete.status_code}): {container_delete.text[:300]}")
        container_id = ""

        _request(session, method="POST", api_base=api_base, path_or_url="/auth/logout", verify_tls=verify_tls)
        print(
            "PASS: synthetic validation succeeded "
            "(login -> VM launch -> Guacamole RDP readiness/frame -> VM teardown -> container launch/websocket readiness/connect/delete)."
        )
        return 0
    finally:
        if vm_id:
            try:
                _request(
                    session,
                    method="DELETE",
                    api_base=api_base,
                    path_or_url=f"/user/pods/{vm_id}",
                    verify_tls=verify_tls,
                )
            except Exception:
                pass
        if container_id:
            try:
                _request(
                    session,
                    method="DELETE",
                    api_base=api_base,
                    path_or_url=f"/user/containers/{container_id}",
                    verify_tls=verify_tls,
                )
            except Exception:
                pass
        try:
            _request(session, method="POST", api_base=api_base, path_or_url="/auth/logout", verify_tls=verify_tls)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
