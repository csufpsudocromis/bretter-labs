from datetime import timedelta

from sqlmodel import Session

from src.db import engine
import src.routes.admin as admin_routes
from src.tables import ImageUploadTask
from src.time_utils import utc_now


def _seed_task(task_id: str, status: str, *, updated_at=None) -> None:
    now = updated_at or utc_now()
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
                created_at=now,
                updated_at=now,
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


def test_upload_watchdog_stale_filter_scans_only_old_tasks(monkeypatch) -> None:
    _seed_task("task-fresh", "finalizing")
    _seed_task("task-stale", "finalizing")

    with Session(engine) as session:
        stale = session.get(ImageUploadTask, "task-stale")
        assert stale is not None
        stale.updated_at = utc_now() - timedelta(seconds=300)
        session.add(stale)
        session.commit()

    seen_ids: list[str] = []

    def _fake_refresh(task, session):
        seen_ids.append(task.id)
        return task

    monkeypatch.setattr(admin_routes, "_refresh_upload_task", _fake_refresh)

    with Session(engine) as session:
        stats = admin_routes.run_upload_task_watchdog(session, max_tasks=10, stale_seconds=60)

    assert seen_ids == ["task-stale"]
    assert stats["scanned"] == 1
    assert stats["errors"] == 0


def test_upload_watchdog_retention_cleanup_removes_old_terminal_tasks(monkeypatch) -> None:
    old_time = utc_now() - timedelta(hours=24)
    _seed_task("task-old-completed", "completed", updated_at=old_time)
    _seed_task("task-old-failed", "failed", updated_at=old_time)
    _seed_task("task-fresh-completed", "completed", updated_at=utc_now())
    monkeypatch.setattr(admin_routes, "_cleanup_task_jobs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(admin_routes, "delete_labimageimport_best_effort", lambda *_args, **_kwargs: None)

    with Session(engine) as session:
        stats = admin_routes.run_upload_task_retention_cleanup(
            session,
            retention_hours=12,
            max_tasks=10,
        )

    assert stats["scanned"] == 2
    assert stats["deleted"] == 2
    assert stats["errors"] == 0
    with Session(engine) as session:
        assert session.get(ImageUploadTask, "task-old-completed") is None
        assert session.get(ImageUploadTask, "task-old-failed") is None
        assert session.get(ImageUploadTask, "task-fresh-completed") is not None
