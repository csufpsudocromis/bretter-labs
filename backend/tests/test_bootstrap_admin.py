import pytest

import src.main as main_module


def _set_valid_production_baseline(monkeypatch):
    monkeypatch.setattr(main_module.settings, "admin_default_username", "admin")
    monkeypatch.setattr(main_module.settings, "admin_default_password", "")
    monkeypatch.setattr(main_module.settings, "production_profile", True)
    monkeypatch.setattr(main_module.settings, "public_scheme", "https")
    monkeypatch.setattr(main_module.settings, "auth_cookie_secure", True)
    monkeypatch.setattr(main_module.settings, "connect_cookie_secure", True)
    monkeypatch.setattr(main_module.settings, "api_docs_enabled", False)
    monkeypatch.setattr(main_module.settings, "vm_connect_insecure_tls", False)
    monkeypatch.setattr(main_module.settings, "container_connect_insecure_tls", False)
    monkeypatch.setattr(main_module.settings, "container_signature_verification_enabled", True)
    monkeypatch.setattr(main_module.settings, "container_signature_key_ref", "/etc/bretter-signing/cosign.pub")
    monkeypatch.setattr(main_module.settings, "container_signature_key_secret_name", "bretter-cosign-public-key")
    monkeypatch.setattr(main_module.settings, "cors_enterprise_profile", True)
    monkeypatch.setattr(main_module.settings, "cors_allowed_origins", "https://10.68.49.250:30073")
    monkeypatch.setattr(main_module.settings, "team_namespace_mode", "per_team")
    monkeypatch.setattr(main_module.settings, "team_namespace_prefix", "labs-team-")
    monkeypatch.setattr(main_module.settings, "team_namespace_bootstrap_enabled", True)
    monkeypatch.setattr(main_module.settings, "orchestration_backend", "dual")
    monkeypatch.setattr(main_module.settings, "kube_node_selector_value", "cbekube2")
    monkeypatch.setattr(main_module.settings, "kube_vm_storage_class", "longhorn-r1")
    monkeypatch.setattr(main_module.settings, "database_pool_size", 20)
    monkeypatch.setattr(main_module.settings, "database_pool_timeout_seconds", 30)
    monkeypatch.setattr(main_module.settings, "database_pool_recycle_seconds", 1800)
    monkeypatch.setattr(main_module.settings, "database_statement_timeout_ms", 15000)
    monkeypatch.setattr(main_module.settings, "database_slow_query_ms", 500)
    monkeypatch.setattr(main_module.settings, "vm_privileged_runtime_isolation_enabled", True)
    monkeypatch.setattr(main_module.settings, "vm_privileged_namespace_prefix", "labs-vm-priv-")
    monkeypatch.setattr(main_module.settings, "kube_use_kvm", True)
    monkeypatch.setattr(main_module.settings, "vm_runner_privileged", False)
    monkeypatch.setattr(main_module.settings, "vm_net_backend", "user")
    monkeypatch.setattr(main_module.settings, "secrets_encryption_key", "A" * 32)


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
    _set_valid_production_baseline(monkeypatch)
    monkeypatch.setattr(main_module.settings, "public_scheme", "http")
    monkeypatch.setattr(main_module.settings, "auth_cookie_secure", False)
    monkeypatch.setattr(main_module.settings, "connect_cookie_secure", False)
    monkeypatch.setattr(main_module.settings, "api_docs_enabled", True)
    monkeypatch.setattr(main_module.settings, "vm_connect_insecure_tls", True)
    monkeypatch.setattr(main_module.settings, "container_connect_insecure_tls", True)
    monkeypatch.setattr(main_module.settings, "container_signature_verification_enabled", False)
    monkeypatch.setattr(main_module.settings, "cors_enterprise_profile", False)
    monkeypatch.setattr(main_module.settings, "cors_allowed_origins", "")
    with pytest.raises(RuntimeError, match="Invalid production startup configuration"):
        main_module._validate_startup_config()


def test_startup_validation_requires_secrets_encryption_key_in_production(monkeypatch):
    _set_valid_production_baseline(monkeypatch)
    monkeypatch.setattr(main_module.settings, "secrets_encryption_key", "")
    with pytest.raises(RuntimeError, match="BLABS_SECRETS_ENCRYPTION_KEY must be set"):
        main_module._validate_startup_config()


def test_startup_validation_rejects_localhost_cors_origins_in_production(monkeypatch):
    _set_valid_production_baseline(monkeypatch)
    monkeypatch.setattr(main_module.settings, "cors_allowed_origins", "https://localhost:30073")
    with pytest.raises(RuntimeError, match="must not include localhost/127.0.0.1"):
        main_module._validate_startup_config()


def test_startup_validation_requires_signature_key_ref_in_production(monkeypatch):
    _set_valid_production_baseline(monkeypatch)
    monkeypatch.setattr(main_module.settings, "container_signature_key_ref", "")
    with pytest.raises(RuntimeError, match="BLABS_CONTAINER_SIGNATURE_KEY_REF must be set"):
        main_module._validate_startup_config()


def test_startup_validation_requires_signature_key_secret_name_for_mounted_key_ref_in_production(monkeypatch):
    _set_valid_production_baseline(monkeypatch)
    monkeypatch.setattr(main_module.settings, "container_signature_key_secret_name", "")
    with pytest.raises(RuntimeError, match="BLABS_CONTAINER_SIGNATURE_KEY_SECRET_NAME must be set"):
        main_module._validate_startup_config()


def test_startup_validation_requires_vm_storage_class_in_production(monkeypatch):
    _set_valid_production_baseline(monkeypatch)
    monkeypatch.setattr(main_module.settings, "kube_vm_storage_class", "")
    with pytest.raises(RuntimeError, match="BLABS_KUBE_VM_STORAGE_CLASS must be set"):
        main_module._validate_startup_config()


def test_startup_validation_requires_team_namespace_bootstrap_in_production(monkeypatch):
    _set_valid_production_baseline(monkeypatch)
    monkeypatch.setattr(main_module.settings, "team_namespace_bootstrap_enabled", False)
    with pytest.raises(RuntimeError, match="BLABS_TEAM_NAMESPACE_BOOTSTRAP_ENABLED must be true"):
        main_module._validate_startup_config()


def test_startup_validation_rejects_db_orchestration_backend_in_production(monkeypatch):
    _set_valid_production_baseline(monkeypatch)
    monkeypatch.setattr(main_module.settings, "orchestration_backend", "db")
    with pytest.raises(RuntimeError, match="BLABS_ORCHESTRATION_BACKEND must be dual or crd"):
        main_module._validate_startup_config()


def test_startup_validation_rejects_production_code_mount_overrides(monkeypatch):
    _set_valid_production_baseline(monkeypatch)
    monkeypatch.setattr(main_module.settings, "allow_code_mount_overrides", False)
    monkeypatch.setattr(main_module, "_find_code_override_mounts", lambda: ["/app/backend/src/routes/user.py"])
    with pytest.raises(RuntimeError, match="Immutable backend deploy violation"):
        main_module._validate_startup_config()


def test_startup_validation_allows_production_code_mount_overrides_with_explicit_opt_in(monkeypatch):
    _set_valid_production_baseline(monkeypatch)
    monkeypatch.setattr(main_module.settings, "allow_code_mount_overrides", True)
    monkeypatch.setattr(main_module, "_find_code_override_mounts", lambda: ["/app/backend/src/routes/user.py"])
    main_module._validate_startup_config()


def test_startup_validation_rejects_invalid_image_import_backend(monkeypatch):
    monkeypatch.setattr(main_module.settings, "admin_default_username", "admin")
    monkeypatch.setattr(main_module.settings, "admin_default_password", "")
    monkeypatch.setattr(main_module.settings, "production_profile", False)
    monkeypatch.setattr(main_module.settings, "orchestration_backend", "db")
    monkeypatch.setattr(main_module.settings, "image_import_backend", "invalid")
    with pytest.raises(RuntimeError, match="BLABS_IMAGE_IMPORT_BACKEND must be one of: db, dual, crd"):
        main_module._validate_startup_config()


def test_startup_validation_requires_labimageimport_crd_fields_when_enabled(monkeypatch):
    monkeypatch.setattr(main_module.settings, "admin_default_username", "admin")
    monkeypatch.setattr(main_module.settings, "admin_default_password", "")
    monkeypatch.setattr(main_module.settings, "production_profile", False)
    monkeypatch.setattr(main_module.settings, "orchestration_backend", "db")
    monkeypatch.setattr(main_module.settings, "image_import_backend", "crd")
    monkeypatch.setattr(main_module.settings, "labimageimport_crd_group", "")
    monkeypatch.setattr(main_module.settings, "labimageimport_crd_version", "v1alpha1")
    monkeypatch.setattr(main_module.settings, "labimageimport_crd_plural", "labimageimports")
    with pytest.raises(RuntimeError, match="BLABS_LABIMAGEIMPORT_CRD_GROUP must be set"):
        main_module._validate_startup_config()
