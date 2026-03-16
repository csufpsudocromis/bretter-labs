import pytest

import src.main as main_module


def _set_enterprise(monkeypatch):
    monkeypatch.setattr(main_module.settings, "cors_enterprise_profile", True)
    monkeypatch.setattr(main_module.settings, "cors_allow_http", False)


def test_enterprise_profile_requires_explicit_origins(monkeypatch):
    _set_enterprise(monkeypatch)
    monkeypatch.setattr(main_module.settings, "cors_allowed_origins", "")
    monkeypatch.setattr(main_module.settings, "cors_allowed_origin_regex", "")

    with pytest.raises(RuntimeError, match="BLABS_CORS_ALLOWED_ORIGINS must be set"):
        main_module._resolve_cors_policy()


def test_enterprise_profile_rejects_origin_regex(monkeypatch):
    _set_enterprise(monkeypatch)
    monkeypatch.setattr(main_module.settings, "cors_allowed_origins", "https://portal.example.edu")
    monkeypatch.setattr(main_module.settings, "cors_allowed_origin_regex", "^https://.*$")

    with pytest.raises(RuntimeError, match="BLABS_CORS_ALLOWED_ORIGIN_REGEX is not permitted"):
        main_module._resolve_cors_policy()


def test_enterprise_profile_rejects_wildcard_methods_and_headers(monkeypatch):
    _set_enterprise(monkeypatch)
    monkeypatch.setattr(main_module.settings, "cors_allowed_origins", "https://portal.example.edu")
    monkeypatch.setattr(main_module.settings, "cors_allowed_origin_regex", "")
    monkeypatch.setattr(main_module.settings, "cors_allowed_methods", "GET,*")
    monkeypatch.setattr(main_module.settings, "cors_allowed_headers", "Content-Type,*")

    with pytest.raises(RuntimeError, match="Wildcard methods are not permitted"):
        main_module._resolve_cors_policy()

    monkeypatch.setattr(main_module.settings, "cors_allowed_methods", "GET,POST")
    with pytest.raises(RuntimeError, match="Wildcard headers are not permitted"):
        main_module._resolve_cors_policy()


def test_enterprise_profile_uses_narrow_defaults(monkeypatch):
    _set_enterprise(monkeypatch)
    monkeypatch.setattr(
        main_module.settings,
        "cors_allowed_origins",
        "https://portal.example.edu,https://labs.example.edu",
    )
    monkeypatch.setattr(main_module.settings, "cors_allowed_origin_regex", "")
    monkeypatch.setattr(main_module.settings, "cors_allowed_methods", "")
    monkeypatch.setattr(main_module.settings, "cors_allowed_headers", "")

    origins, origin_regex, methods, headers = main_module._resolve_cors_policy()
    assert origins == ["https://portal.example.edu", "https://labs.example.edu"]
    assert origin_regex is None
    assert methods == ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    assert headers == ["Accept", "Content-Type", "Authorization"]


def test_non_enterprise_profile_keeps_regex_and_wildcards(monkeypatch):
    monkeypatch.setattr(main_module.settings, "cors_enterprise_profile", False)
    monkeypatch.setattr(main_module.settings, "cors_allowed_origins", "https://portal.example.edu")
    monkeypatch.setattr(main_module.settings, "cors_allowed_origin_regex", "^https://([a-z0-9-]+\\.)?example\\.edu$")

    origins, origin_regex, methods, headers = main_module._resolve_cors_policy()
    assert origins == ["https://portal.example.edu"]
    assert origin_regex == "^https://([a-z0-9-]+\\.)?example\\.edu$"
    assert methods == ["*"]
    assert headers == ["*"]
