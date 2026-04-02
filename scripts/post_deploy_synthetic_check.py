#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import requests

ALLOWED_IMAGE_UPLOAD_SUFFIXES = {".vhd", ".vhdx", ".qcow", ".qcow2", ".vdi"}


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


def _run_role_authz_check(
    *,
    session: requests.Session,
    api_base: str,
    verify_tls: bool,
    username: str,
    password: str,
    expected_role: str,
    route_expectations: list[tuple[str, bool]],
) -> None:
    login = _request(
        session,
        method="POST",
        api_base=api_base,
        path_or_url="/auth/login",
        verify_tls=verify_tls,
        json_payload={"username": username, "password": password},
    )
    if login.status_code != 200:
        _fail(f"{expected_role} role check login failed ({login.status_code}): {login.text[:300]}")

    try:
        me = _request(session, method="GET", api_base=api_base, path_or_url="/auth/me", verify_tls=verify_tls)
        if me.status_code != 200:
            _fail(f"{expected_role} role check /auth/me failed ({me.status_code}): {me.text[:300]}")
        me_role = str((me.json() or {}).get("role") or "").strip().lower()
        if expected_role and me_role and me_role != expected_role:
            _fail(f"{expected_role} role check mismatch: /auth/me reported role {me_role!r}")

        for path, should_allow in route_expectations:
            resp = _request(session, method="GET", api_base=api_base, path_or_url=path, verify_tls=verify_tls)
            if should_allow and resp.status_code != 200:
                _fail(f"{expected_role} role check expected allow for {path} but got {resp.status_code}")
            if not should_allow and resp.status_code not in {401, 403}:
                _fail(f"{expected_role} role check expected deny for {path} but got {resp.status_code}")
    finally:
        try:
            _request(session, method="POST", api_base=api_base, path_or_url="/auth/logout", verify_tls=verify_tls)
        except Exception:
            pass


def _fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def _resolve_image_upload_fixture(require_upload_check: bool) -> tuple[Path | None, str, bool]:
    fixture_path = str(os.environ.get("SYNTHETIC_IMAGE_UPLOAD_FILE") or "").strip()
    fixture_name_override = str(os.environ.get("SYNTHETIC_IMAGE_UPLOAD_NAME") or "").strip()
    if fixture_path:
        candidate = Path(fixture_path).expanduser()
        if not candidate.is_file():
            _fail(f"SYNTHETIC_IMAGE_UPLOAD_FILE does not exist or is not a file: {candidate}")
        filename = fixture_name_override or candidate.name
        if Path(filename).suffix.lower() not in ALLOWED_IMAGE_UPLOAD_SUFFIXES:
            _fail(
                "SYNTHETIC_IMAGE_UPLOAD_NAME/SYNTHETIC_IMAGE_UPLOAD_FILE must end with one of "
                f"{sorted(ALLOWED_IMAGE_UPLOAD_SUFFIXES)}."
            )
        return candidate, filename, False
    if not require_upload_check:
        return None, "", False
    qemu_img = shutil.which("qemu-img")
    if not qemu_img:
        _fail(
            "SYNTHETIC_REQUIRE_IMAGE_UPLOAD_CHECK=1 requires SYNTHETIC_IMAGE_UPLOAD_FILE "
            "or qemu-img available in PATH."
        )
    fixture_dir = Path(tempfile.mkdtemp(prefix="blabs-synth-upload-"))
    candidate = fixture_dir / "synthetic-upload.qcow2"
    try:
        subprocess.run(
            [qemu_img, "create", "-f", "qcow2", str(candidate), "64M"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(fixture_dir, ignore_errors=True)
        _fail(f"failed to auto-generate qcow2 fixture with qemu-img: {(exc.stderr or exc.stdout or '').strip()[:300]}")
    return candidate, candidate.name, True


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


def _wait_for_upload_task(
    *,
    session: requests.Session,
    api_base: str,
    verify_tls: bool,
    task_id: str,
    deadline_epoch: float,
    poll_seconds: float,
) -> dict[str, Any]:
    last_status = ""
    last_detail = ""
    while time.time() < deadline_epoch:
        resp = _request(
            session,
            method="GET",
            api_base=api_base,
            path_or_url=f"/admin/images/upload-tasks/{task_id}",
            verify_tls=verify_tls,
        )
        if resp.status_code == 200:
            payload = resp.json() or {}
            last_status = str(payload.get("status") or "").strip().lower()
            last_detail = str(payload.get("detail") or payload.get("error") or "").strip()
            if last_status in {"completed", "failed"}:
                return payload
            time.sleep(poll_seconds)
            continue
        if resp.status_code == 404:
            time.sleep(poll_seconds)
            continue
        _fail(f"upload task refresh failed ({resp.status_code}): {resp.text[:300]}")
    _fail(f"timeout waiting for upload task {task_id}: status={last_status or 'unknown'} detail={last_detail[:200]}")
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


def _wait_for_container_ws_readiness_status(
    *,
    session: requests.Session,
    api_base: str,
    verify_tls: bool,
    container_id: str,
    deadline_epoch: float,
    poll_seconds: float,
) -> tuple[bool, str]:
    last_detail = ""
    while time.time() < deadline_epoch:
        try:
            readiness = _request(
                session,
                method="GET",
                api_base=api_base,
                path_or_url=f"/user/containers/{container_id}/connect-readiness",
                verify_tls=verify_tls,
            )
        except requests.RequestException as exc:
            last_detail = f"connect-readiness request error: {type(exc).__name__}"
            time.sleep(poll_seconds)
            continue
        if readiness.status_code == 200:
            payload = readiness.json() or {}
            if bool(payload.get("ready")):
                return True, ""
            last_detail = str(payload.get("detail") or "").strip()
        else:
            last_detail = f"connect-readiness returned {readiness.status_code}"
        time.sleep(poll_seconds)
    detail = last_detail[:300] if last_detail else "no readiness detail"
    return False, detail


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


def _run_image_upload_finalize_check(
    *,
    session: requests.Session,
    api_base: str,
    verify_tls: bool,
    upload_path: Path,
    upload_filename: str,
    deadline_epoch: float,
    poll_seconds: float,
) -> None:
    with upload_path.open("rb") as handle:
        upload_resp = session.post(
            urljoin(api_base + "/", "admin/images"),
            files={"file": (upload_filename, handle, "application/octet-stream")},
            timeout=180,
            verify=verify_tls,
        )
    if upload_resp.status_code != 202:
        _fail(f"image upload submit failed ({upload_resp.status_code}): {upload_resp.text[:300]}")
    upload_payload = upload_resp.json() or {}
    task_id = str(upload_payload.get("task_id") or "").strip()
    if not task_id:
        _fail("image upload response missing task_id")
    task_payload = _wait_for_upload_task(
        session=session,
        api_base=api_base,
        verify_tls=verify_tls,
        task_id=task_id,
        deadline_epoch=deadline_epoch,
        poll_seconds=poll_seconds,
    )
    status = str(task_payload.get("status") or "").strip().lower()
    if status != "completed":
        detail = str(task_payload.get("error") or task_payload.get("detail") or "upload finalize failed").strip()
        _fail(f"image upload/finalize failed: {detail[:300]}")
    image_id = str(task_payload.get("image_id") or "").strip()
    if not image_id:
        _fail("image upload task completed but image_id was missing")
    image_delete = _request(
        session,
        method="DELETE",
        api_base=api_base,
        path_or_url=f"/admin/images/{image_id}",
        verify_tls=verify_tls,
    )
    if image_delete.status_code not in {204, 404}:
        _fail(f"image cleanup delete failed ({image_delete.status_code}): {image_delete.text[:300]}")


def _cleanup_existing_user_labs(
    *,
    session: requests.Session,
    api_base: str,
    verify_tls: bool,
    deadline_epoch: float,
    poll_seconds: float,
) -> None:
    vm_ids: list[str] = []
    container_ids: list[str] = []

    vm_list = _request(session, method="GET", api_base=api_base, path_or_url="/user/pods", verify_tls=verify_tls)
    if vm_list.status_code == 200:
        for row in vm_list.json() or []:
            vm_id = str(row.get("id") or "").strip()
            if vm_id:
                vm_ids.append(vm_id)
                _request(
                    session,
                    method="DELETE",
                    api_base=api_base,
                    path_or_url=f"/user/pods/{vm_id}",
                    verify_tls=verify_tls,
                )
    else:
        _fail(f"failed to list existing VMs for cleanup ({vm_list.status_code}): {vm_list.text[:300]}")

    container_list = _request(
        session, method="GET", api_base=api_base, path_or_url="/user/containers", verify_tls=verify_tls
    )
    if container_list.status_code == 200:
        for row in container_list.json() or []:
            container_id = str(row.get("id") or "").strip()
            if container_id:
                container_ids.append(container_id)
                _request(
                    session,
                    method="DELETE",
                    api_base=api_base,
                    path_or_url=f"/user/containers/{container_id}",
                    verify_tls=verify_tls,
                )
    else:
        _fail(
            f"failed to list existing containers for cleanup ({container_list.status_code}): {container_list.text[:300]}"
        )

    for vm_id in vm_ids:
        _wait_deleted_or_released(
            session=session,
            api_base=api_base,
            verify_tls=verify_tls,
            list_path="/user/pods",
            instance_id=vm_id,
            deadline_epoch=min(deadline_epoch, time.time() + 180),
            poll_seconds=poll_seconds,
        )
    for container_id in container_ids:
        _wait_deleted_or_released(
            session=session,
            api_base=api_base,
            verify_tls=verify_tls,
            list_path="/user/containers",
            instance_id=container_id,
            deadline_epoch=min(deadline_epoch, time.time() + 180),
            poll_seconds=poll_seconds,
        )


def _container_idle_bridge_url(connect_url: str) -> str:
    parsed = urlparse(str(connect_url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        _fail(f"container connect_url is not absolute: {connect_url!r}")
    base_path = parsed.path or "/"
    if not base_path.endswith("/"):
        base_path = base_path + "/"
    bridge_path = base_path + "__blabs_idle_bridge.js"
    return urlunparse(parsed._replace(path=bridge_path))


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _select_container_template(
    templates: list[dict[str, Any]], *, preferred_id: str, preferred_name: str
) -> dict[str, Any]:
    if preferred_id:
        matched = next((row for row in templates if str(row.get("id") or "").strip() == preferred_id), None)
        if matched is not None:
            return matched
    preferred_name_normalized = preferred_name.strip().lower()
    if preferred_name_normalized:
        matched = next(
            (row for row in templates if preferred_name_normalized in str(row.get("name") or "").strip().lower()),
            None,
        )
        if matched is not None:
            return matched

    synthetic_name_match = next(
        (
            row
            for row in templates
            if "synthetic" in str(row.get("name") or "").strip().lower()
            and "nginx" in str(row.get("name") or "").strip().lower()
        ),
        None,
    )
    if synthetic_name_match is not None:
        return synthetic_name_match

    return min(
        templates,
        key=lambda row: (
            _int_or_default(row.get("cpu_millicores"), 10_000_000),
            _int_or_default(row.get("memory_mb"), 10_000_000),
            str(row.get("name") or "").strip().lower(),
        ),
    )


def main() -> int:
    api_base = str(os.environ.get("SYNTHETIC_API_BASE") or "").strip().rstrip("/")
    username = str(os.environ.get("SYNTHETIC_USERNAME") or "").strip()
    password = str(os.environ.get("SYNTHETIC_PASSWORD") or "").strip()
    verify_tls = _bool_env("SYNTHETIC_VERIFY_TLS", True)
    require_templates = _bool_env("SYNTHETIC_REQUIRE_TEMPLATES", True)
    require_rdp_template = _bool_env("SYNTHETIC_REQUIRE_RDP_TEMPLATE", False)
    require_image_upload_check = _bool_env("SYNTHETIC_REQUIRE_IMAGE_UPLOAD_CHECK", False)
    lab_admin_username = str(os.environ.get("SYNTHETIC_LAB_ADMIN_USERNAME") or "").strip()
    lab_admin_password = str(os.environ.get("SYNTHETIC_LAB_ADMIN_PASSWORD") or "").strip()
    namespace_admin_username = str(os.environ.get("SYNTHETIC_NAMESPACE_ADMIN_USERNAME") or "").strip()
    namespace_admin_password = str(os.environ.get("SYNTHETIC_NAMESPACE_ADMIN_PASSWORD") or "").strip()
    platform_admin_username = str(os.environ.get("SYNTHETIC_PLATFORM_ADMIN_USERNAME") or "").strip()
    platform_admin_password = str(os.environ.get("SYNTHETIC_PLATFORM_ADMIN_PASSWORD") or "").strip()
    preferred_container_template_id = str(os.environ.get("SYNTHETIC_CONTAINER_TEMPLATE_ID") or "").strip()
    preferred_container_template_name = str(
        os.environ.get("SYNTHETIC_CONTAINER_TEMPLATE_NAME") or "Synthetic Nginx"
    ).strip()
    timeout_seconds = max(120, int(os.environ.get("SYNTHETIC_TIMEOUT_SECONDS") or "900"))
    poll_seconds = max(1.0, float(os.environ.get("SYNTHETIC_POLL_SECONDS") or "3"))
    rdp_marker_timeout_seconds = max(30, int(os.environ.get("SYNTHETIC_RDP_MARKER_TIMEOUT_SECONDS") or "180"))
    container_ws_timeout_seconds = max(30, int(os.environ.get("SYNTHETIC_CONTAINER_WS_TIMEOUT_SECONDS") or "180"))
    container_launch_retry_limit = max(1, int(os.environ.get("SYNTHETIC_CONTAINER_LAUNCH_RETRY_LIMIT") or "3"))
    container_launch_retry_backoff_seconds = max(
        1, int(os.environ.get("SYNTHETIC_CONTAINER_LAUNCH_RETRY_BACKOFF_SECONDS") or "20")
    )
    image_upload_timeout_seconds = max(60, int(os.environ.get("SYNTHETIC_IMAGE_UPLOAD_TIMEOUT_SECONDS") or "1200"))
    post_vm_grace_seconds = max(0, int(os.environ.get("SYNTHETIC_POST_VM_GRACE_SECONDS") or "20"))
    single_lab_limit_message = "you already have a virtual lab running"

    if not api_base:
        _fail("SYNTHETIC_API_BASE is required.")
    if not username or not password:
        _fail("SYNTHETIC_USERNAME and SYNTHETIC_PASSWORD are required.")
    if not verify_tls:
        requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
    if bool(lab_admin_username) != bool(lab_admin_password):
        _fail("SYNTHETIC_LAB_ADMIN_USERNAME and SYNTHETIC_LAB_ADMIN_PASSWORD must be set together.")
    if bool(namespace_admin_username) != bool(namespace_admin_password):
        _fail("SYNTHETIC_NAMESPACE_ADMIN_USERNAME and SYNTHETIC_NAMESPACE_ADMIN_PASSWORD must be set together.")
    if bool(platform_admin_username) != bool(platform_admin_password):
        _fail("SYNTHETIC_PLATFORM_ADMIN_USERNAME and SYNTHETIC_PLATFORM_ADMIN_PASSWORD must be set together.")

    session = requests.Session()
    session.headers.update({"Accept": "application/json"})
    deadline = time.time() + timeout_seconds
    vm_id = ""
    container_id = ""
    upload_fixture_path, upload_fixture_name, upload_fixture_generated = _resolve_image_upload_fixture(
        require_upload_check=require_image_upload_check
    )

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

        _cleanup_existing_user_labs(
            session=session,
            api_base=api_base,
            verify_tls=verify_tls,
            deadline_epoch=deadline,
            poll_seconds=poll_seconds,
        )

        if upload_fixture_path and upload_fixture_name:
            _run_image_upload_finalize_check(
                session=session,
                api_base=api_base,
                verify_tls=verify_tls,
                upload_path=upload_fixture_path,
                upload_filename=upload_fixture_name,
                deadline_epoch=min(deadline, time.time() + image_upload_timeout_seconds),
                poll_seconds=poll_seconds,
            )

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
        selected_container_template = _select_container_template(
            container_templates,
            preferred_id=preferred_container_template_id,
            preferred_name=preferred_container_template_name,
        )
        container_template_id = str((selected_container_template or {}).get("id") or "").strip()
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
        if post_vm_grace_seconds > 0:
            time.sleep(post_vm_grace_seconds)

        last_container_ws_detail = ""
        container_ready = False
        for launch_attempt in range(1, container_launch_retry_limit + 1):
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
            container_ready, last_container_ws_detail = _wait_for_container_ws_readiness_status(
                session=session,
                api_base=api_base,
                verify_tls=verify_tls,
                container_id=container_id,
                deadline_epoch=min(deadline, time.time() + container_ws_timeout_seconds),
                poll_seconds=poll_seconds,
            )
            if container_ready:
                break

            container_ws_detail = str(last_container_ws_detail or "").strip().lower()
            container_delete = _request(
                session,
                method="DELETE",
                api_base=api_base,
                path_or_url=f"/user/containers/{container_id}",
                verify_tls=verify_tls,
            )
            if container_delete.status_code not in {204, 404}:
                _fail(
                    f"container cleanup after failed readiness failed ({container_delete.status_code}): "
                    f"{container_delete.text[:300]}"
                )
            _wait_deleted_or_released(
                session=session,
                api_base=api_base,
                verify_tls=verify_tls,
                list_path="/user/containers",
                instance_id=container_id,
                deadline_epoch=min(deadline, time.time() + 180),
                poll_seconds=poll_seconds,
            )
            container_id = ""
            if (
                "scheduling container pod" in container_ws_detail
                and launch_attempt < container_launch_retry_limit
                and time.time() < deadline
            ):
                time.sleep(container_launch_retry_backoff_seconds)
                continue
            break

        if not container_ready:
            detail = last_container_ws_detail[:300] if last_container_ws_detail else "no readiness detail"
            _fail(f"timeout waiting for container websocket readiness: {detail}")

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

        idle_bridge_url = _container_idle_bridge_url(connect_url)
        idle_bridge = _request(
            session,
            method="GET",
            api_base=api_base,
            path_or_url=idle_bridge_url,
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
        role_checks_run: list[str] = []

        if lab_admin_username and lab_admin_password:
            _run_role_authz_check(
                session=session,
                api_base=api_base,
                verify_tls=verify_tls,
                username=lab_admin_username,
                password=lab_admin_password,
                expected_role="lab_admin",
                route_expectations=[
                    ("/admin/images", True),
                    ("/admin/users", False),
                    ("/admin/settings/concurrency", False),
                ],
            )
            role_checks_run.append("lab_admin authz")

        if namespace_admin_username and namespace_admin_password:
            _run_role_authz_check(
                session=session,
                api_base=api_base,
                verify_tls=verify_tls,
                username=namespace_admin_username,
                password=namespace_admin_password,
                expected_role="namespace_admin",
                route_expectations=[
                    ("/admin/images", True),
                    ("/admin/users", True),
                    ("/admin/settings/concurrency", True),
                ],
            )
            role_checks_run.append("namespace_admin authz")

        if platform_admin_username and platform_admin_password:
            _run_role_authz_check(
                session=session,
                api_base=api_base,
                verify_tls=verify_tls,
                username=platform_admin_username,
                password=platform_admin_password,
                expected_role="platform_admin",
                route_expectations=[
                    ("/admin/images", True),
                    ("/admin/users", True),
                    ("/admin/settings/concurrency", True),
                ],
            )
            role_checks_run.append("platform_admin authz")

        flow_parts = ["login"]
        if upload_fixture_path and upload_fixture_name:
            flow_parts.append("admin image upload/finalize/delete")
        flow_parts.extend(
            [
                "VM launch",
                "Guacamole RDP readiness/frame",
                "VM teardown",
                "container launch/websocket readiness/connect/delete",
            ]
        )
        flow_parts.extend(role_checks_run)
        print("PASS: synthetic validation succeeded (" + " -> ".join(flow_parts) + ").")
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
        if upload_fixture_generated and upload_fixture_path:
            shutil.rmtree(str(upload_fixture_path.parent), ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
