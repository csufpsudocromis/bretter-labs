from __future__ import annotations

import os
import sys

from kubernetes import client, config
from kubernetes.client import ApiException
from sqlmodel import select

from ..config import settings
from ..db import session_scope
from ..tables import Instance


def _load_kube() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def _phase_to_db_status(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"running", "pending", "completed", "stopped", "failed"}:
        return normalized
    if normalized in {"starting", "building"}:
        return "pending"
    return "unknown"


def main() -> int:
    max_missing = max(0, int(os.environ.get("PARITY_MAX_MISSING", "0") or "0"))
    max_mismatch = max(0, int(os.environ.get("PARITY_MAX_MISMATCH", "0") or "0"))
    mode = str(getattr(settings, "orchestration_backend", "db") or "db").strip().lower()
    if mode not in {"dual", "crd"}:
        print(f"orchestration_parity: skipped (ORCHESTRATION_BACKEND={mode or 'db'})")
        return 0

    db_rows: list[Instance]
    with session_scope() as session:
        db_rows = session.exec(select(Instance)).all()
    db_map = {row.id: str(row.status or "").strip().lower() for row in db_rows}

    _load_kube()
    custom = client.CustomObjectsApi()
    try:
        payload = custom.list_namespaced_custom_object(
            group=str(settings.labinstance_crd_group or "labs.bretter.io"),
            version=str(settings.labinstance_crd_version or "v1alpha1"),
            namespace=settings.kube_namespace,
            plural=str(settings.labinstance_crd_plural or "labinstances"),
        )
    except ApiException as exc:
        print(f"ERROR: unable to list LabInstance CRDs: {exc.status} {exc.reason}", file=sys.stderr)
        return 1

    crd_items = payload.get("items") if isinstance(payload, dict) else []
    if not isinstance(crd_items, list):
        crd_items = []
    crd_map = {}
    for item in crd_items:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        status = item.get("status") if isinstance(item.get("status"), dict) else {}
        name = str(metadata.get("name") or "").strip()
        if not name:
            continue
        crd_map[name] = _phase_to_db_status(status.get("phase"))

    missing_in_crd = sorted(inst_id for inst_id in db_map if inst_id not in crd_map)
    missing_in_db = sorted(inst_id for inst_id in crd_map if inst_id not in db_map)

    status_mismatch = []
    for inst_id, db_status in db_map.items():
        crd_status = crd_map.get(inst_id)
        if not crd_status:
            continue
        if db_status != crd_status:
            status_mismatch.append((inst_id, db_status, crd_status))

    print(
        "orchestration_parity: "
        f"db_instances={len(db_map)} crd_instances={len(crd_map)} "
        f"missing_in_crd={len(missing_in_crd)} missing_in_db={len(missing_in_db)} "
        f"status_mismatch={len(status_mismatch)}"
    )
    if missing_in_crd:
        print("missing_in_crd_ids=" + ",".join(missing_in_crd[:10]))
    if missing_in_db:
        print("missing_in_db_ids=" + ",".join(missing_in_db[:10]))
    if status_mismatch:
        sample = ",".join(f"{i}:{db}->{crd}" for i, db, crd in status_mismatch[:10])
        print("status_mismatch_sample=" + sample)

    if len(missing_in_crd) > max_missing:
        print(
            f"ERROR: missing_in_crd threshold breached ({len(missing_in_crd)} > {max_missing}).",
            file=sys.stderr,
        )
        return 1
    if len(status_mismatch) > max_mismatch:
        print(
            f"ERROR: status_mismatch threshold breached ({len(status_mismatch)} > {max_mismatch}).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
