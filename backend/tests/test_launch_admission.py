from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.services import launch_admission


def _node(
    name: str,
    *,
    ready: bool = True,
    disk_pressure: bool = False,
    memory_pressure: bool = False,
    pid_pressure: bool = False,
):
    def cond(cond_type: str, state: bool):
        return SimpleNamespace(type=cond_type, status="True" if state else "False")

    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        status=SimpleNamespace(
            conditions=[
                cond("Ready", ready),
                cond("DiskPressure", disk_pressure),
                cond("MemoryPressure", memory_pressure),
                cond("PIDPressure", pid_pressure),
            ]
        ),
    )


def _pvc(name: str, phase: str, created_at: datetime):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, creation_timestamp=created_at),
        status=SimpleNamespace(phase=phase),
    )


class _FakeCore:
    def __init__(self, *, nodes=None, pvcs=None):
        self._nodes = list(nodes or [])
        self._pvcs = list(pvcs or [])

    def list_node(self, label_selector: str = ""):
        return SimpleNamespace(items=self._nodes)

    def list_namespaced_persistent_volume_claim(self, namespace: str):
        return SimpleNamespace(items=self._pvcs)


class _FakeKube:
    def __init__(self, core: _FakeCore):
        self._core = core

    def _client(self):
        return self._core


def test_node_launch_admission_rejects_when_no_ready_nodes() -> None:
    kube = _FakeKube(_FakeCore(nodes=[_node("node-a", ready=False)]))
    ok, detail = launch_admission.evaluate_node_launch_admission(kube)
    assert ok is False
    assert "No candidate nodes are Ready" in detail


def test_node_launch_admission_accepts_healthy_subset() -> None:
    kube = _FakeKube(
        _FakeCore(
            nodes=[
                _node("node-a", ready=True),
                _node("node-b", ready=True, disk_pressure=True),
            ]
        )
    )
    ok, detail = launch_admission.evaluate_node_launch_admission(kube)
    assert ok is True
    assert "1/2" in detail
    assert "node-b" in detail


def test_vm_storage_admission_blocks_on_stale_pending_backlog(monkeypatch) -> None:
    monkeypatch.setattr(launch_admission.settings, "launch_admission_pending_pvc_block_minutes", 10, raising=False)
    monkeypatch.setattr(launch_admission.settings, "launch_admission_pending_pvc_block_count", 2, raising=False)
    now = datetime.now(timezone.utc)
    kube = _FakeKube(
        _FakeCore(
            pvcs=[
                _pvc("pvc-old-a", "Pending", now - timedelta(minutes=25)),
                _pvc("pvc-old-b", "Pending", now - timedelta(minutes=12)),
                _pvc("pvc-new", "Pending", now - timedelta(minutes=3)),
            ]
        )
    )
    ok, detail = launch_admission.evaluate_vm_storage_launch_admission(kube, namespace="labs")
    assert ok is False
    assert "storage provisioning appears degraded" in detail
    assert "pvc-old-a" in detail


def test_vm_storage_admission_warns_but_allows_small_backlog(monkeypatch) -> None:
    monkeypatch.setattr(launch_admission.settings, "launch_admission_pending_pvc_block_minutes", 10, raising=False)
    monkeypatch.setattr(launch_admission.settings, "launch_admission_pending_pvc_block_count", 3, raising=False)
    now = datetime.now(timezone.utc)
    kube = _FakeKube(_FakeCore(pvcs=[_pvc("pvc-old-a", "Pending", now - timedelta(minutes=11))]))
    ok, detail = launch_admission.evaluate_vm_storage_launch_admission(kube, namespace="labs")
    assert ok is True
    assert "Storage admission warning" in detail
