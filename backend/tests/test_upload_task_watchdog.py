from sqlmodel import Session

from src.db import engine
import src.routes.admin as admin_routes
from src.tables import ImageUploadTask
from src.time_utils import utc_now


def _seed_task(task_id: str, status: str) -> None:
    with Session(engine) as session:
        session.add(
            ImageUploadTask(
                id=task_id,
                original_filename=f"{task_id}.vdi",
                filename=f"{task_id}.vdi",
                size_bytes=1024,
                status=status,
                stage=status,
                detail="seed",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        session.commit()


def test_upload_watchdog_scans_only_active_tasks(monkeypatch) -> None:
    _seed_task("task-active", "finalizing")
    _seed_task("task-done", "completed")

    seen_ids: list[str] = []

    def _fake_refresh(task, session):
        seen_ids.append(task.id)
        task.status = "completed"
        task.stage = "completed"
        task.updated_at = utc_now()
        session.add(task)
        session.commit()
        session.refresh(task)
        return task

    monkeypatch.setattr(admin_routes, "_refresh_upload_task", _fake_refresh)

    with Session(engine) as session:
        stats = admin_routes.run_upload_task_watchdog(session, max_tasks=10)

    assert seen_ids == ["task-active"]
    assert stats["scanned"] == 1
    assert stats["completed"] == 1
    assert stats["failed"] == 0
    assert stats["errors"] == 0


def test_upload_watchdog_marks_task_failed_when_refresh_raises(monkeypatch) -> None:
    _seed_task("task-error", "finalizing")

    def _boom(task, session):
        raise RuntimeError("simulated watchdog refresh failure")

    monkeypatch.setattr(admin_routes, "_refresh_upload_task", _boom)

    with Session(engine) as session:
        stats = admin_routes.run_upload_task_watchdog(session, max_tasks=10)

    assert stats["scanned"] == 1
    assert stats["errors"] == 1
    with Session(engine) as session:
        task = session.get(ImageUploadTask, "task-error")
        assert task is not None
        assert task.status == "failed"
        assert task.stage == "failed"
        assert task.detail == "Watchdog refresh failed"
        assert "simulated watchdog refresh failure" in (task.error_message or "")
