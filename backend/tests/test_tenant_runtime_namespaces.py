import src.services.tenant_context as tenant_context
from src.tables import User


def _user(team: str = "math") -> User:
    return User(username="alice", password_hash="x", team=team)


def test_vm_runtime_namespace_uses_privileged_prefix_when_required(monkeypatch):
    monkeypatch.setattr(tenant_context.settings, "team_namespace_mode", "per_team")
    monkeypatch.setattr(tenant_context.settings, "team_namespace_prefix", "labs-team-")
    monkeypatch.setattr(tenant_context.settings, "vm_privileged_namespace_prefix", "labs-vm-priv-")
    monkeypatch.setattr(tenant_context.settings, "vm_privileged_runtime_isolation_enabled", True)
    monkeypatch.setattr(tenant_context.settings, "kube_use_kvm", True)
    monkeypatch.setattr(tenant_context.settings, "vm_runner_privileged", False)
    monkeypatch.setattr(tenant_context.settings, "vm_net_backend", "user")

    assert tenant_context.vm_runtime_namespace_for_user(_user("math")) == "labs-vm-priv-math"


def test_vm_runtime_namespace_falls_back_to_team_namespace_when_not_privileged(monkeypatch):
    monkeypatch.setattr(tenant_context.settings, "team_namespace_mode", "per_team")
    monkeypatch.setattr(tenant_context.settings, "team_namespace_prefix", "labs-team-")
    monkeypatch.setattr(tenant_context.settings, "vm_privileged_namespace_prefix", "labs-vm-priv-")
    monkeypatch.setattr(tenant_context.settings, "vm_privileged_runtime_isolation_enabled", True)
    monkeypatch.setattr(tenant_context.settings, "kube_use_kvm", False)
    monkeypatch.setattr(tenant_context.settings, "vm_runner_privileged", False)
    monkeypatch.setattr(tenant_context.settings, "vm_net_backend", "user")

    assert tenant_context.vm_runtime_namespace_for_user(_user("science")) == "labs-team-science"


def test_vm_runtime_namespace_respects_isolation_toggle(monkeypatch):
    monkeypatch.setattr(tenant_context.settings, "team_namespace_mode", "per_team")
    monkeypatch.setattr(tenant_context.settings, "team_namespace_prefix", "labs-team-")
    monkeypatch.setattr(tenant_context.settings, "vm_privileged_namespace_prefix", "labs-vm-priv-")
    monkeypatch.setattr(tenant_context.settings, "vm_privileged_runtime_isolation_enabled", False)
    monkeypatch.setattr(tenant_context.settings, "kube_use_kvm", True)
    monkeypatch.setattr(tenant_context.settings, "vm_runner_privileged", False)
    monkeypatch.setattr(tenant_context.settings, "vm_net_backend", "user")

    assert tenant_context.vm_runtime_namespace_for_user(_user("design")) == "labs-team-design"
