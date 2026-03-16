import pytest

import src.main as main_module


def test_bootstrap_password_uses_configured_secret(monkeypatch):
    monkeypatch.setattr(main_module.settings, "admin_default_password", "one-time-secret")
    assert main_module._resolve_admin_bootstrap_password() == "one-time-secret"


def test_bootstrap_password_requires_secret_when_admin_missing(monkeypatch):
    monkeypatch.setattr(main_module.settings, "admin_default_password", "")
    with pytest.raises(RuntimeError, match="BLABS_ADMIN_DEFAULT_PASSWORD"):
        main_module._resolve_admin_bootstrap_password()
