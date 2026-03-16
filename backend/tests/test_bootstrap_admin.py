import pytest

import src.main as main_module


def test_bootstrap_password_uses_configured_secret(monkeypatch):
    monkeypatch.setattr(main_module.settings, "admin_default_password", "one-time-secret")
    assert main_module._resolve_admin_bootstrap_password() == "one-time-secret"


def test_bootstrap_password_requires_secret_when_admin_missing(monkeypatch):
    monkeypatch.setattr(main_module.settings, "admin_default_password", "")
    with pytest.raises(RuntimeError, match="BLABS_ADMIN_DEFAULT_PASSWORD"):
        main_module._resolve_admin_bootstrap_password()


def test_startup_validation_rejects_empty_admin_username(monkeypatch):
    monkeypatch.setattr(main_module.settings, "admin_default_username", "")
    monkeypatch.setattr(main_module.settings, "production_profile", False)
    with pytest.raises(RuntimeError, match="BLABS_ADMIN_DEFAULT_USERNAME"):
        main_module._validate_startup_config()


def test_startup_validation_rejects_weak_bootstrap_password(monkeypatch):
    monkeypatch.setattr(main_module.settings, "admin_default_username", "admin")
    monkeypatch.setattr(main_module.settings, "admin_default_password", "admin")
    monkeypatch.setattr(main_module.settings, "production_profile", False)
    with pytest.raises(RuntimeError, match="cannot use weak defaults"):
        main_module._validate_startup_config()


def test_startup_validation_enforces_production_profile(monkeypatch):
    monkeypatch.setattr(main_module.settings, "admin_default_username", "admin")
    monkeypatch.setattr(main_module.settings, "admin_default_password", "")
    monkeypatch.setattr(main_module.settings, "production_profile", True)
    monkeypatch.setattr(main_module.settings, "public_scheme", "http")
    monkeypatch.setattr(main_module.settings, "auth_cookie_secure", False)
    monkeypatch.setattr(main_module.settings, "connect_cookie_secure", False)
    monkeypatch.setattr(main_module.settings, "api_docs_enabled", True)
    monkeypatch.setattr(main_module.settings, "vm_connect_insecure_tls", True)
    monkeypatch.setattr(main_module.settings, "container_connect_insecure_tls", True)
    monkeypatch.setattr(main_module.settings, "cors_enterprise_profile", False)
    monkeypatch.setattr(main_module.settings, "cors_allowed_origins", "")
    with pytest.raises(RuntimeError, match="Invalid production startup configuration"):
        main_module._validate_startup_config()
