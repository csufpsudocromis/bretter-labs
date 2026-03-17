from src.config import settings
from src.routes.admin import _list_pvc_files


def test_list_pvc_files_creates_missing_storage_root(tmp_path, monkeypatch) -> None:
    storage_root = tmp_path / "missing-images-root"
    monkeypatch.setattr(settings, "storage_root", str(storage_root))
    assert not storage_root.exists()

    files = _list_pvc_files()

    assert files == []
    assert storage_root.exists()
    assert storage_root.is_dir()


def test_list_pvc_files_filters_allowed_extensions(tmp_path, monkeypatch) -> None:
    storage_root = tmp_path / "images"
    storage_root.mkdir(parents=True)
    (storage_root / "linux.qcow2").write_bytes(b"abc")
    (storage_root / "windows.vhdx").write_bytes(b"abcd")
    (storage_root / "notes.txt").write_text("ignore", encoding="utf-8")
    monkeypatch.setattr(settings, "storage_root", str(storage_root))

    files = _list_pvc_files()
    names = {item["name"] for item in files}

    assert names == {"linux.qcow2", "windows.vhdx"}
