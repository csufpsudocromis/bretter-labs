from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

from src.auth import hash_password
from src.db import engine
from src.rbac import Role
from src.routes import admin as admin_routes
from src.tables import Image, TeamQuota, User
from src.time_utils import utc_now


def test_quota_teams_lists_user_and_quota_teams(login_admin: TestClient) -> None:
    with Session(engine) as session:
        session.add(
            User(
                username="red-user",
                password_hash=hash_password("password"),
                role=Role.USER,
                team="red",
                is_admin=False,
                force_password_change=False,
            )
        )
        session.add(
            TeamQuota(
                id="quota-blue-labs",
                team="blue",
                namespace="labs",
                enabled=True,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        session.commit()

    response = login_admin.get("/admin/quota-teams")
    assert response.status_code == 200, response.text
    teams = set(response.json())
    assert {"default", "red", "blue"}.issubset(teams)


def test_admin_audit_events_record_quota_and_settings_changes(login_admin: TestClient) -> None:
    created = login_admin.post(
        "/admin/team-quotas",
        json={
            "team": "ops",
            "namespace": "labs",
            "max_concurrent_labs": 2,
            "max_cpu_millicores": None,
            "max_memory_mb": None,
            "max_storage_gib": None,
            "idle_timeout_minutes_cap": None,
            "enabled": True,
        },
    )
    assert created.status_code == 201, created.text

    updated = login_admin.post("/admin/settings/idle-timeout", json={"idle_timeout_minutes": 25})
    assert updated.status_code == 200, updated.text

    events = login_admin.get("/admin/audit-events", params={"limit": 50})
    assert events.status_code == 200, events.text
    payload = events.json()
    assert any(item["target_type"] == "team_quota" and item["action"] == "create" for item in payload)
    assert any(item["target_type"] == "settings_idle_timeout" and item["action"] == "update" for item in payload)
    assert any(item["actor"] == "admin" for item in payload)


def test_admin_image_upload_finalize_smoke(login_admin: TestClient, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(admin_routes, "_image_dir", lambda: tmp_path)

    def _fake_ensure_finalize(task):  # noqa: ANN001
        task.finalize_job = "img-finalize-smoke"
        task.status = "finalizing"
        task.stage = "finalizing"
        task.progress_percent = 0
        task.detail = "Finalizing image format/checksum on cluster"
        task.updated_at = utc_now()

    def _fake_refresh(task, session):  # noqa: ANN001
        if task.status != "completed":
            task.status = "completed"
            task.stage = "completed"
            task.progress_percent = 100
            task.detail = "Image ready"
            task.error_message = None
            task.source_pvc = task.source_pvc or "golden-images-smoke"
            task.checksum = task.checksum or ("a" * 64)
            existing = session.get(Image, task.image_id)
            if not existing:
                session.add(
                    Image(
                        id=task.image_id or "img-smoke",
                        name=task.filename,
                        filename=task.filename,
                        source_pvc=task.source_pvc,
                        checksum=task.checksum,
                        size_bytes=max(1, int(task.size_bytes or 1)),
                        created_at=utc_now(),
                    )
                )
            session.add(task)
            session.commit()
            session.refresh(task)
        return task

    monkeypatch.setattr(admin_routes, "_ensure_upload_task_finalize_job", _fake_ensure_finalize)
    monkeypatch.setattr(admin_routes, "_refresh_upload_task", _fake_refresh)

    upload = login_admin.post(
        "/admin/images",
        files={"file": ("smoke-upload.qcow2", b"not-real-qcow2", "application/octet-stream")},
    )
    assert upload.status_code == 202, upload.text
    task_id = upload.json()["task_id"]

    status = login_admin.get(f"/admin/images/upload-tasks/{task_id}")
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "completed"
    assert status.json()["stage"] == "completed"
    assert status.json()["progress_percent"] == 100
