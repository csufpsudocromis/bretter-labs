from fastapi.testclient import TestClient

from src.routes import admin_containers


def test_verify_image_signature_calls_kube_service(monkeypatch):
    monkeypatch.setattr(admin_containers.settings, "container_signature_verification_enabled", True)
    captured: dict[str, str] = {}

    def _fake_verify(image_ref: str) -> None:
        captured["image_ref"] = image_ref
        return None

    monkeypatch.setattr(admin_containers.kube, "verify_container_image_signature", _fake_verify)

    admin_containers._verify_image_signature("docker.io/library/nginx:1.27")
    assert captured["image_ref"] == "docker.io/library/nginx:1.27"


def test_verify_image_signature_allows_unsigned_image(monkeypatch):
    monkeypatch.setattr(admin_containers.settings, "container_signature_verification_enabled", True)
    monkeypatch.setattr(
        admin_containers.kube,
        "verify_container_image_signature",
        lambda image_ref: "Image has no signatures; continuing with warning-only policy.",
    )
    warning = admin_containers._verify_image_signature("docker.io/library/nginx:1.27")
    assert warning == "Image has no signatures; continuing with warning-only policy."


def test_create_container_image_returns_signature_warning_for_unsigned_image(login_admin: TestClient, monkeypatch):
    monkeypatch.setattr(admin_containers.settings, "container_signature_verification_enabled", True)
    monkeypatch.setattr(
        admin_containers.kube,
        "verify_container_image_signature",
        lambda image_ref: "Image has no signatures; continuing with warning-only policy.",
    )
    response = login_admin.post(
        "/admin/container-images",
        json={"name": "Unsigned Nginx", "image_ref": "docker.io/library/nginx:1.27"},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["name"] == "Unsigned Nginx"
    assert payload["signature_warning"] == "Image has no signatures; continuing with warning-only policy."


def test_create_container_image_rejects_local_dev_ref_in_production(login_admin: TestClient, monkeypatch):
    monkeypatch.setattr(admin_containers.settings, "production_profile", True)
    monkeypatch.setattr(admin_containers.settings, "container_allowed_registries", "*")

    response = login_admin.post(
        "/admin/container-images",
        json={"name": "Local Dev", "image_ref": "localhost/dev/image:local-1"},
    )
    assert response.status_code == 422, response.text
    assert "local/dev image references are not allowed in production profile" in response.text
