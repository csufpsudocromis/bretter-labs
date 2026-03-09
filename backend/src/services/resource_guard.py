import logging
from decimal import Decimal

from kubernetes import client
from kubernetes.utils import parse_quantity

from ..config import settings
from .kubernetes import kube

logger = logging.getLogger(__name__)


def _to_int_quantity(value: object) -> int:
    if value is None:
        return 0
    text = str(value).strip()
    if not text:
        return 0
    try:
        parsed = parse_quantity(text)
    except Exception:
        return 0
    if isinstance(parsed, Decimal):
        return int(parsed)
    return int(float(parsed))


def _parse_cpu_m(value: object) -> int:
    if value is None:
        return 0
    text = str(value).strip()
    if not text:
        return 0
    # parse_quantity("1") => 1 core, parse_quantity("100m") => 0.1 core
    try:
        parsed = parse_quantity(text)
    except Exception:
        return 0
    as_float = float(parsed)
    return int(as_float * 1000)


def _parse_bytes(value: object) -> int:
    return max(0, _to_int_quantity(value))


def cluster_requested_allocatable(
    core: client.CoreV1Api | None = None,
) -> tuple[int, int, int, int]:
    core_v1 = core or kube._client()
    nodes = core_v1.list_node().items
    pods = core_v1.list_pod_for_all_namespaces().items

    alloc_cpu_m = 0
    alloc_memory_bytes = 0
    for node in nodes:
        alloc = node.status.allocatable or {}
        alloc_cpu_m += _parse_cpu_m(alloc.get("cpu"))
        alloc_memory_bytes += _parse_bytes(alloc.get("memory"))

    requested_cpu_m = 0
    requested_memory_bytes = 0
    for pod in pods:
        phase = str(getattr(pod.status, "phase", "") or "").lower()
        if phase in {"succeeded", "failed"}:
            continue
        for container in getattr(pod.spec, "containers", []) or []:
            req = (container.resources and container.resources.requests) or {}
            requested_cpu_m += _parse_cpu_m(req.get("cpu"))
            requested_memory_bytes += _parse_bytes(req.get("memory"))

    return alloc_cpu_m, alloc_memory_bytes, requested_cpu_m, requested_memory_bytes


def check_launch_headroom(request_cpu_m: int, request_memory_mb: int) -> str | None:
    reserved_cpu_m = max(0, int(settings.launch_reserved_cpu_m or 0))
    reserved_memory_mb = max(0, int(settings.launch_reserved_memory_mb or 0))
    request_cpu_m = max(0, int(request_cpu_m or 0))
    request_memory_bytes = max(0, int(request_memory_mb or 0)) * 1024 * 1024
    reserved_memory_bytes = reserved_memory_mb * 1024 * 1024

    if request_cpu_m <= 0 and request_memory_bytes <= 0:
        return None

    try:
        alloc_cpu_m, alloc_mem_bytes, req_cpu_m, req_mem_bytes = cluster_requested_allocatable()
    except Exception as exc:
        logger.warning("Resource headroom check skipped: %s", exc)
        return None

    safe_cpu_m = max(0, alloc_cpu_m - req_cpu_m - reserved_cpu_m)
    safe_mem_bytes = max(0, alloc_mem_bytes - req_mem_bytes - reserved_memory_bytes)

    if request_cpu_m <= safe_cpu_m and request_memory_bytes <= safe_mem_bytes:
        return None

    return (
        "Waiting for available resources (reserved node headroom guardrail). "
        f"Requested launch needs {request_cpu_m}m CPU and {int(request_memory_bytes / (1024 * 1024))}Mi memory, "
        f"but safe headroom is {safe_cpu_m}m CPU and {int(safe_mem_bytes / (1024 * 1024))}Mi memory "
        f"after reserving {reserved_cpu_m}m CPU and {reserved_memory_mb}Mi memory."
    )
