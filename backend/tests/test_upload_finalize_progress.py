from src.config import settings
from src.routes.admin import (
    _finalize_in_checksum_phase,
    _parse_finalize_progress_percent,
    _requested_upload_pvc_gi,
    _retry_backoff_seconds,
    _task_stage_progress,
)
from src.tables import ImageUploadTask


def test_parse_finalize_progress_percent_from_qemu_progress_log() -> None:
    log_data = "(1.23/100%)\r(48.95/100%)\r(83.01/100%)\n"
    assert _parse_finalize_progress_percent(log_data) == 83


def test_parse_finalize_progress_percent_returns_none_without_progress_marker() -> None:
    assert _parse_finalize_progress_percent("BLABS_OUTPUT_FILENAME=image.qcow2\n") is None


def test_parse_finalize_progress_percent_clamps_value_bounds() -> None:
    assert _parse_finalize_progress_percent("(-1/100%)") is None
    assert _parse_finalize_progress_percent("(120.50/100%)") == 100


def test_finalize_in_checksum_phase_detects_phase_marker() -> None:
    assert _finalize_in_checksum_phase("BLABS_PHASE=checksum\n")
    assert not _finalize_in_checksum_phase("BLABS_PHASE=convert\n")


def test_requested_upload_pvc_gi_applies_minimum_floor(monkeypatch) -> None:
    monkeypatch.setattr(settings, "min_upload_pvc_gib", 80)
    assert _requested_upload_pvc_gi(1) == 80


def test_requested_upload_pvc_gi_uses_larger_computed_size(monkeypatch) -> None:
    monkeypatch.setattr(settings, "min_upload_pvc_gib", 40)
    assert _requested_upload_pvc_gi(120 * 1024**3) == 121


def test_finalize_retry_backoff_is_bounded() -> None:
    assert _retry_backoff_seconds(1) >= 5
    assert _retry_backoff_seconds(5) >= _retry_backoff_seconds(2)


def test_task_stage_progress_defaults_importing_to_zero_until_reported() -> None:
    task = ImageUploadTask(
        id="task-importing-zero",
        original_filename="sample.vdi",
        filename="sample.vdi",
        size_bytes=1024,
        status="importing",
        stage="importing",
        progress_percent=None,
    )
    stage, progress = _task_stage_progress(task)
    assert stage == "importing"
    assert progress == 0


def test_task_stage_progress_defaults_finalizing_to_zero_until_reported() -> None:
    task = ImageUploadTask(
        id="task-finalizing-zero",
        original_filename="sample.vdi",
        filename="sample.vdi",
        size_bytes=1024,
        status="finalizing",
        stage="finalizing",
        progress_percent=None,
    )
    stage, progress = _task_stage_progress(task)
    assert stage == "finalizing"
    assert progress == 0
