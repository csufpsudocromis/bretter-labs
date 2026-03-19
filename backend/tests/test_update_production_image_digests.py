from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _script_path() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / "update_production_image_digests.py"


def test_update_production_image_digests_rewrites_target_keys(tmp_path: Path) -> None:
    values = tmp_path / "values-production.yaml"
    values.write_text(
        "\n".join(
            [
                "appTemplateValues:",
                "  BACKEND_IMAGE: ghcr.io/example/backend@sha256:" + ("1" * 64),
                "  FRONTEND_IMAGE: ghcr.io/example/frontend@sha256:" + ("2" * 64),
                "  RUNNER_IMAGE: ghcr.io/example/runner@sha256:" + ("3" * 64),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    cmd = [
        sys.executable,
        str(_script_path()),
        "--values-file",
        str(values),
        "--backend-image",
        "ghcr.io/new/backend@sha256:" + ("a" * 64),
        "--frontend-image",
        "ghcr.io/new/frontend@sha256:" + ("b" * 64),
        "--runner-image",
        "ghcr.io/new/runner@sha256:" + ("c" * 64),
    ]
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout

    updated = values.read_text(encoding="utf-8")
    assert "BACKEND_IMAGE: ghcr.io/new/backend@sha256:" + ("a" * 64) in updated
    assert "FRONTEND_IMAGE: ghcr.io/new/frontend@sha256:" + ("b" * 64) in updated
    assert "RUNNER_IMAGE: ghcr.io/new/runner@sha256:" + ("c" * 64) in updated


def test_update_production_image_digests_rejects_non_digest_refs(tmp_path: Path) -> None:
    values = tmp_path / "values-production.yaml"
    values.write_text(
        "\n".join(
            [
                "appTemplateValues:",
                "  BACKEND_IMAGE: ghcr.io/example/backend@sha256:" + ("1" * 64),
                "  FRONTEND_IMAGE: ghcr.io/example/frontend@sha256:" + ("2" * 64),
                "  RUNNER_IMAGE: ghcr.io/example/runner@sha256:" + ("3" * 64),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    cmd = [
        sys.executable,
        str(_script_path()),
        "--values-file",
        str(values),
        "--backend-image",
        "ghcr.io/new/backend:latest",
        "--frontend-image",
        "ghcr.io/new/frontend@sha256:" + ("b" * 64),
        "--runner-image",
        "ghcr.io/new/runner@sha256:" + ("c" * 64),
    ]
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    assert result.returncode != 0
