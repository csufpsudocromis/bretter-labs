from __future__ import annotations

from datetime import datetime, timezone

from kubernetes.client import ApiException

from ..config import settings


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def evaluate_node_launch_admission(kube_service) -> tuple[bool, str]:
    core = kube_service._client()
    if not hasattr(core, "list_node"):
        return True, "Node admission check skipped: Kubernetes client does not expose list_node."
    selector_key = str(
        getattr(settings, "kube_node_selector_key", "kubernetes.io/hostname") or "kubernetes.io/hostname"
    ).strip()
    selector_value = str(getattr(settings, "kube_node_selector_value", "") or "").strip()
    label_selector = f"{selector_key}={selector_value}" if selector_value else ""
    selector_detail = f"{selector_key}={selector_value}" if selector_value else "all nodes"

    try:
        nodes = list(core.list_node(label_selector=label_selector).items)
    except ApiException as exc:
        detail = exc.reason or str(exc.status)
        return False, f"Node admission check failed while listing nodes: {detail}"
    except Exception as exc:
        return False, f"Node admission check failed while listing nodes: {exc}"

    if not nodes:
        return False, f"No candidate nodes match launch selector ({selector_detail})."

    ready_nodes = []
    healthy_nodes = []
    pressured_nodes = []
    for node in nodes:
        name = str(getattr(node.metadata, "name", "") or "").strip() or "<unknown>"
        conditions = {
            str(cond.type or "").lower(): str(cond.status or "") for cond in list(node.status.conditions or [])
        }
        ready = conditions.get("ready", "").lower() == "true"
        disk_pressure = conditions.get("diskpressure", "").lower() == "true"
        memory_pressure = conditions.get("memorypressure", "").lower() == "true"
        pid_pressure = conditions.get("pidpressure", "").lower() == "true"
        if ready:
            ready_nodes.append(name)
        if ready and not (disk_pressure or memory_pressure or pid_pressure):
            healthy_nodes.append(name)
        elif ready and (disk_pressure or memory_pressure or pid_pressure):
            pressured_nodes.append(name)

    if not ready_nodes:
        return False, f"No candidate nodes are Ready for launches ({selector_detail})."
    if not healthy_nodes:
        listed = ", ".join(pressured_nodes[:4]) or "all candidate nodes"
        return False, f"Launch blocked: candidate nodes are under pressure (Disk/Memory/PID). Affected: {listed}"

    base = f"Node admission passed: {len(healthy_nodes)}/{len(nodes)} candidate nodes are Ready without pressure."
    if pressured_nodes:
        return True, base + f" Pressured nodes excluded: {', '.join(pressured_nodes[:4])}."
    return True, base


def evaluate_vm_storage_launch_admission(kube_service, *, namespace: str) -> tuple[bool, str]:
    core = kube_service._client()
    if not hasattr(core, "list_namespaced_persistent_volume_claim"):
        return True, "PVC admission check skipped: Kubernetes client does not expose PVC list API."
    pending_age_minutes = max(1, int(getattr(settings, "launch_admission_pending_pvc_block_minutes", 10) or 10))
    pending_block_count = max(1, int(getattr(settings, "launch_admission_pending_pvc_block_count", 2) or 2))
    now = datetime.now(timezone.utc)

    try:
        pvcs = list(core.list_namespaced_persistent_volume_claim(namespace=namespace).items)
    except ApiException as exc:
        detail = exc.reason or str(exc.status)
        return False, f"PVC admission check failed while listing PVCs in {namespace}: {detail}"
    except Exception as exc:
        return False, f"PVC admission check failed while listing PVCs in {namespace}: {exc}"

    stale_pending: list[tuple[str, int]] = []
    for pvc in pvcs:
        phase = str(getattr(getattr(pvc, "status", None), "phase", "") or "").strip().lower()
        if phase != "pending":
            continue
        created = _as_utc(getattr(getattr(pvc, "metadata", None), "creation_timestamp", None))
        if created is None:
            continue
        age_minutes = max(0, int((now - created).total_seconds() // 60))
        if age_minutes >= pending_age_minutes:
            name = str(getattr(getattr(pvc, "metadata", None), "name", "") or "").strip() or "<unknown>"
            stale_pending.append((name, age_minutes))

    if len(stale_pending) >= pending_block_count:
        sample = ", ".join(f"{name}({age}m)" for name, age in stale_pending[:4])
        return (
            False,
            "Launch blocked: storage provisioning appears degraded "
            f"({len(stale_pending)} pending PVCs older than {pending_age_minutes}m in {namespace}: {sample}).",
        )
    if stale_pending:
        sample = ", ".join(f"{name}({age}m)" for name, age in stale_pending[:3])
        return (
            True,
            f"Storage admission warning: {len(stale_pending)} pending PVC(s) older than {pending_age_minutes}m "
            f"in {namespace} ({sample}).",
        )
    return True, "PVC admission passed: no stale pending PVC backlog detected."
