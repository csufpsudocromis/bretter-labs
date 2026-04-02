from types import SimpleNamespace

import src.services.tenant_namespace_bootstrap as bootstrap


class _FakeKubeService:
    def _client(self):
        return SimpleNamespace(api_client=object())

    def _networking_client(self):
        return object()


def test_bootstrap_syncs_runtime_critical_secrets_and_configmaps(monkeypatch) -> None:
    monkeypatch.setattr(bootstrap.settings, "team_namespace_bootstrap_enabled", True)
    monkeypatch.setattr(bootstrap.settings, "team_namespace_mode", "per_team")
    monkeypatch.setattr(bootstrap.settings, "kube_namespace", "labs")
    monkeypatch.setattr(bootstrap.settings, "image_pull_secret", "ghcr-creds")
    monkeypatch.setattr(bootstrap.settings, "kube_tls_secret", "bretter-tls")
    monkeypatch.setattr(bootstrap.settings, "runtime_secrets_secret_name", "bretter-runtime-secrets")
    monkeypatch.setattr(bootstrap.settings, "container_signature_key_secret_name", "bretter-cosign-public-key")
    monkeypatch.setattr(bootstrap.settings, "kube_spice_embed_configmap", "spice-embed")

    monkeypatch.setattr(bootstrap.client, "RbacAuthorizationV1Api", lambda _api_client: object())
    monkeypatch.setattr(bootstrap, "_upsert_namespace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bootstrap, "_upsert_role", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bootstrap, "_upsert_role_binding", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bootstrap, "_upsert_resource_quota", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bootstrap, "_upsert_limit_range", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bootstrap, "_upsert_network_policies", lambda *_args, **_kwargs: None)

    synced_secrets: list[tuple[str, str, str]] = []
    synced_configmaps: list[tuple[str, str, str]] = []

    def _record_secret(_core_api, *, source_namespace: str, target_namespace: str, name: str) -> None:
        synced_secrets.append((source_namespace, target_namespace, name))

    def _record_configmap(_core_api, *, source_namespace: str, target_namespace: str, name: str) -> None:
        synced_configmaps.append((source_namespace, target_namespace, name))

    monkeypatch.setattr(bootstrap, "_sync_secret", _record_secret)
    monkeypatch.setattr(bootstrap, "_sync_configmap", _record_configmap)

    bootstrap.ensure_team_runtime_namespace(
        _FakeKubeService(),
        team="default",
        namespace="labs-team-default",
        privileged_runtime=False,
    )

    synced_secret_names = {name for _, _, name in synced_secrets}
    assert synced_secret_names == {
        "ghcr-creds",
        "bretter-tls",
        "bretter-runtime-secrets",
        "bretter-cosign-public-key",
    }
    assert {name for _, _, name in synced_configmaps} == {"spice-embed"}
