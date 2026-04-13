from sqlmodel import Session

from src.db import engine
from src.routes import admin as admin_routes
from src.tables import ImageUploadTask


def test_cleanup_upload_task_deletes_labimageimport_shadow(login_admin, monkeypatch) -> None:
    task_id = "ops-cleanup-crd-shadow"
    with Session(engine) as session:
        session.add(
            ImageUploadTask(
                id=task_id,
                original_filename="win11.vdi",
                filename="",
                namespace="labs",
                status="failed",
                stage="failed",
                detail="test failure",
            )
        )
        session.commit()

    deleted_task_ids: list[str] = []

    def _capture_delete(task_id_value: str) -> None:
        deleted_task_ids.append(task_id_value)

    monkeypatch.setattr(admin_routes, "_cleanup_task_jobs", lambda _task: None)
    monkeypatch.setattr(admin_routes, "delete_labimageimport_best_effort", _capture_delete)

    response = login_admin.delete(f"/admin/operations/upload-tasks/{task_id}")
    assert response.status_code == 200, response.text
    assert response.json().get("ok") is True

    with Session(engine) as session:
        assert session.get(ImageUploadTask, task_id) is None

    assert deleted_task_ids == [task_id]
