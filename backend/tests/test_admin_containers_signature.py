import os
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.routes import admin_containers


def test_verify_image_signature_uses_writable_sigstore_cache(monkeypatch):
    monkeypatch.setattr(admin_containers.settings, "container_signature_verification_enabled", True)
    monkeypatch.setattr(admin_containers.settings, "container_signature_key_ref", "")

    captured: dict[str, object] = {}

    def _fake_run(cmd, check, capture_output, text, timeout, env):  # noqa: ANN001
        captured["cmd"] = cmd
        captured["env"] = env
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(admin_containers.subprocess, "run", _fake_run)

    admin_containers._verify_image_signature("docker.io/library/nginx:1.27")

    assert captured["cmd"] == [
        "cosign",
        "verify",
        "--certificate-identity-regexp",
        ".*",
        "--certificate-oidc-issuer-regexp",
        ".*",
        "docker.io/library/nginx:1.27",
    ]
    env = captured["env"]
    assert isinstance(env, dict)
    assert str(env.get("HOME", "")).startswith("/tmp/blabs-cosign/")
    assert str(env.get("XDG_CACHE_HOME", "")).startswith("/tmp/blabs-cosign/")
    assert os.path.isdir(str(env["HOME"]))
    assert os.path.isdir(str(env["XDG_CACHE_HOME"]))


def test_verify_image_signature_allows_unsigned_image(monkeypatch):
    monkeypatch.setattr(admin_containers.settings, "container_signature_verification_enabled", True)
    monkeypatch.setattr(admin_containers.settings, "container_signature_key_ref", "/etc/bretter-signing/cosign.pub")

    def _fake_run(cmd, check, capture_output, text, timeout, env):  # noqa: ANN001
        return SimpleNamespace(
            returncode=1,
            stderr="Error: no signatures found\nerror during command execution: no signatures found",
            stdout="",
        )

    monkeypatch.setattr(admin_containers.subprocess, "run", _fake_run)
    warning = admin_containers._verify_image_signature("docker.io/library/nginx:1.27")
    assert warning == "Image has no signatures; continuing with warning-only policy."


def test_create_container_image_returns_signature_warning_for_unsigned_image(login_admin: TestClient, monkeypatch):
    monkeypatch.setattr(admin_containers.settings, "container_signature_verification_enabled", True)
    monkeypatch.setattr(admin_containers.settings, "container_signature_key_ref", "/etc/bretter-signing/cosign.pub")

    def _fake_run(cmd, check, capture_output, text, timeout, env):  # noqa: ANN001
        return SimpleNamespace(
            returncode=1,
            stderr="Error: no signatures found\nerror during command execution: no signatures found",
            stdout="",
        )

    monkeypatch.setattr(admin_containers.subprocess, "run", _fake_run)
    response = login_admin.post(
        "/admin/container-images",
        json={"name": "Unsigned Nginx", "image_ref": "docker.io/library/nginx:1.27"},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["name"] == "Unsigned Nginx"
    assert payload["signature_warning"] == "Image has no signatures; continuing with warning-only policy."
