from src.routes.admin import _parse_finalize_progress_percent


def test_parse_finalize_progress_percent_from_qemu_progress_log() -> None:
    log_data = "(1.23/100%)\r(48.95/100%)\r(83.01/100%)\n"
    assert _parse_finalize_progress_percent(log_data) == 83


def test_parse_finalize_progress_percent_returns_none_without_progress_marker() -> None:
    assert _parse_finalize_progress_percent("BLABS_OUTPUT_FILENAME=image.qcow2\n") is None


def test_parse_finalize_progress_percent_clamps_value_bounds() -> None:
    assert _parse_finalize_progress_percent("(-1/100%)") is None
    assert _parse_finalize_progress_percent("(120.50/100%)") == 100
