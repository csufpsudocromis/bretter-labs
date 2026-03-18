import pytest

import src.routes.admin as admin_routes
from src.routes.admin import _ensure_source_filename_alias_on_pvc


def test_ensure_source_filename_alias_requires_claim_name() -> None:
    with pytest.raises(RuntimeError, match="claim name is required"):
        _ensure_source_filename_alias_on_pvc("", "disk.img", "win11.qcow2")


def test_ensure_source_filename_alias_noop_when_names_match(monkeypatch) -> None:
    called = False

    def _fake_with_pvc_helper(*_args, **_kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(admin_routes, "_with_pvc_helper", _fake_with_pvc_helper)
    _ensure_source_filename_alias_on_pvc("img-src-a661df9b", "disk.img", "disk.img")
    assert called is False


def test_ensure_source_filename_alias_creates_symlink_command(monkeypatch) -> None:
    calls: list[tuple[list[str], str | None]] = []

    def _fake_with_pvc_helper(command: list[str], *, claim_name: str | None = None, **_kwargs):
        calls.append((command, claim_name))
        return None

    monkeypatch.setattr(admin_routes, "_with_pvc_helper", _fake_with_pvc_helper)
    _ensure_source_filename_alias_on_pvc("img-src-a661df9b", "/tmp/disk.img", "../win11.qcow2")

    assert len(calls) == 1
    command, claim_name = calls[0]
    assert claim_name == "img-src-a661df9b"
    assert command[:2] == ["/bin/sh", "-c"]
    assert "ln -s disk.img win11.qcow2" in command[2]
