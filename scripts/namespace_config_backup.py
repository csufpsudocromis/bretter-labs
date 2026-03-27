#!/usr/bin/env python3
"""Export and restore namespace-scoped control-plane configuration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import inspect
from sqlmodel import Session, select

from src.db import engine
from src.rbac import Role, role_for_user
from src.services.team_quotas import normalize_namespace, normalize_team
from src.services.tenant_context import normalize_namespace_scopes, set_user_namespace_scopes, user_namespace_scopes
from src.tables import ContainerTemplate, ManagedNamespace, TeamQuota, Template, User
from src.time_utils import utc_now

MANAGED_NAMESPACE_FIELDS = [
    "namespace",
    "team_label",
    "security_profile",
    "enforce_network_policies",
    "max_pods",
    "max_services",
    "max_persistent_volume_claims",
    "requests_cpu",
    "limits_cpu",
    "requests_memory",
    "limits_memory",
    "requests_storage",
    "limit_min_cpu",
    "limit_min_memory",
    "limit_default_request_cpu",
    "limit_default_request_memory",
    "limit_default_cpu",
    "limit_default_memory",
    "limit_max_cpu",
    "limit_max_memory",
    "idle_timeout_minutes_default",
    "vm_auto_delete_minutes_default",
    "container_auto_delete_minutes_default",
    "queue_max_pending",
    "upload_max_bytes",
    "enabled",
]


def _parse_enabled_namespaces(raw: str | None) -> list[str]:
    payload = str(raw or "").strip()
    if not payload:
        return []
    try:
        decoded = json.loads(payload)
    except Exception:
        return []
    if not isinstance(decoded, list):
        return []
    normalized = normalize_namespace_scopes([str(item) for item in decoded])
    return sorted(set(normalized))


def _serialize_enabled_namespaces(values: list[str]) -> str:
    normalized = normalize_namespace_scopes(values)
    return json.dumps(sorted(set(normalized)), separators=(",", ":"))


def _managed_namespace_to_dict(row: ManagedNamespace) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in MANAGED_NAMESPACE_FIELDS:
        out[field] = getattr(row, field)
    return out


def _has_table(name: str) -> bool:
    return bool(inspect(engine).has_table(name))


def _team_quota_to_dict(row: TeamQuota) -> dict[str, Any]:
    return {
        "namespace": normalize_namespace(row.namespace),
        "max_concurrent_labs": row.max_concurrent_labs,
        "max_cpu_millicores": row.max_cpu_millicores,
        "max_memory_mb": row.max_memory_mb,
        "max_storage_gib": row.max_storage_gib,
        "idle_timeout_minutes_cap": row.idle_timeout_minutes_cap,
        "enabled": bool(row.enabled),
    }


def export_config(output: Path) -> None:
    managed_rows = []
    team_quota_rows = []
    template_rows = []
    container_template_rows = []
    users = []
    with Session(engine) as session:
        if _has_table("managednamespace"):
            managed_rows = session.exec(select(ManagedNamespace)).all()
        if _has_table("teamquota"):
            team_quota_rows = session.exec(select(TeamQuota)).all()
        if _has_table("template"):
            template_rows = session.exec(select(Template)).all()
        if _has_table("containertemplate"):
            container_template_rows = session.exec(select(ContainerTemplate)).all()
        if _has_table("user"):
            users = session.exec(select(User)).all()

    payload = {
        "schema_version": 1,
        "exported_at": utc_now().isoformat(),
        "managed_namespaces": sorted(
            [_managed_namespace_to_dict(row) for row in managed_rows],
            key=lambda item: item["namespace"],
        ),
        "namespace_quotas": sorted(
            [_team_quota_to_dict(row) for row in team_quota_rows],
            key=lambda item: item["namespace"],
        ),
        "template_enabled_namespaces": sorted(
            [
                {
                    "id": row.id,
                    "name": row.name,
                    "namespace": normalize_namespace(row.namespace),
                    "enabled_namespaces": _parse_enabled_namespaces(getattr(row, "enabled_namespaces_json", "[]")),
                }
                for row in template_rows
            ],
            key=lambda item: item["id"],
        ),
        "container_template_enabled_namespaces": sorted(
            [
                {
                    "id": row.id,
                    "name": row.name,
                    "namespace": normalize_namespace(row.namespace),
                    "enabled_namespaces": _parse_enabled_namespaces(getattr(row, "enabled_namespaces_json", "[]")),
                }
                for row in container_template_rows
            ],
            key=lambda item: item["id"],
        ),
        "namespace_admin_user_scopes": sorted(
            [
                {
                    "username": user.username,
                    "scopes": user_namespace_scopes(user),
                }
                for user in users
                if role_for_user(user) == Role.NAMESPACE_ADMIN
            ],
            key=lambda item: item["username"],
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote namespace config backup: {output}")


def _upsert_managed_namespace(session: Session, item: dict[str, Any]) -> bool:
    namespace = normalize_namespace(item.get("namespace"))
    if not namespace:
        return False
    row = session.exec(select(ManagedNamespace).where(ManagedNamespace.namespace == namespace)).first()
    created = False
    if row is None:
        row = ManagedNamespace(id=str(uuid4()), namespace=namespace, created_at=utc_now(), updated_at=utc_now())
        created = True
    for field in MANAGED_NAMESPACE_FIELDS:
        if field == "namespace":
            continue
        if field not in item:
            continue
        setattr(row, field, item[field])
    row.namespace = namespace
    row.team_label = normalize_team(getattr(row, "team_label", "default"))
    row.updated_at = utc_now()
    session.add(row)
    return created


def _upsert_namespace_quota(session: Session, item: dict[str, Any]) -> bool:
    namespace = normalize_namespace(item.get("namespace"))
    if not namespace:
        return False
    row = session.exec(
        select(TeamQuota).where(TeamQuota.namespace == namespace).where(TeamQuota.team == normalize_team("default"))
    ).first()
    created = False
    if row is None:
        row = TeamQuota(
            id=str(uuid4()),
            team=normalize_team("default"),
            namespace=namespace,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        created = True
    for field in (
        "max_concurrent_labs",
        "max_cpu_millicores",
        "max_memory_mb",
        "max_storage_gib",
        "idle_timeout_minutes_cap",
        "enabled",
    ):
        if field in item:
            setattr(row, field, item[field])
    row.updated_at = utc_now()
    session.add(row)
    return created


def _apply_template_bindings(session: Session, items: list[dict[str, Any]]) -> int:
    updated = 0
    for item in items:
        template_id = str(item.get("id") or "").strip()
        if not template_id:
            continue
        row = session.get(Template, template_id)
        if row is None:
            continue
        raw_values = item.get("enabled_namespaces")
        if not isinstance(raw_values, list):
            continue
        row.enabled_namespaces_json = _serialize_enabled_namespaces([str(v) for v in raw_values])
        session.add(row)
        updated += 1
    return updated


def _apply_container_template_bindings(session: Session, items: list[dict[str, Any]]) -> int:
    updated = 0
    for item in items:
        template_id = str(item.get("id") or "").strip()
        if not template_id:
            continue
        row = session.get(ContainerTemplate, template_id)
        if row is None:
            continue
        raw_values = item.get("enabled_namespaces")
        if not isinstance(raw_values, list):
            continue
        row.enabled_namespaces_json = _serialize_enabled_namespaces([str(v) for v in raw_values])
        session.add(row)
        updated += 1
    return updated


def _apply_namespace_admin_scopes(session: Session, items: list[dict[str, Any]]) -> int:
    updated = 0
    for item in items:
        username = str(item.get("username") or "").strip()
        if not username:
            continue
        row = session.get(User, username)
        if row is None or role_for_user(row) != Role.NAMESPACE_ADMIN:
            continue
        raw_scopes = item.get("scopes")
        if not isinstance(raw_scopes, list):
            continue
        set_user_namespace_scopes(row, [str(v) for v in raw_scopes])
        session.add(row)
        updated += 1
    return updated


def import_config(input_path: Path, *, dry_run: bool) -> None:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    managed_items = payload.get("managed_namespaces") if isinstance(payload, dict) else None
    quota_items = payload.get("namespace_quotas") if isinstance(payload, dict) else None
    template_bindings = payload.get("template_enabled_namespaces") if isinstance(payload, dict) else None
    container_template_bindings = (
        payload.get("container_template_enabled_namespaces") if isinstance(payload, dict) else None
    )
    namespace_admin_scopes = payload.get("namespace_admin_user_scopes") if isinstance(payload, dict) else None

    if not isinstance(managed_items, list):
        managed_items = []
    if not isinstance(quota_items, list):
        quota_items = []
    if not isinstance(template_bindings, list):
        template_bindings = []
    if not isinstance(container_template_bindings, list):
        container_template_bindings = []
    if not isinstance(namespace_admin_scopes, list):
        namespace_admin_scopes = []

    required_tables = ["managednamespace", "teamquota", "template", "containertemplate", "user"]
    missing = [name for name in required_tables if not _has_table(name)]
    if missing:
        raise SystemExit(
            "missing required tables for import: "
            + ", ".join(missing)
            + ". Run migrations/setup before importing namespace config."
        )

    with Session(engine) as session:
        managed_created = 0
        managed_updated = 0
        for item in managed_items:
            if not isinstance(item, dict):
                continue
            if _upsert_managed_namespace(session, item):
                managed_created += 1
            else:
                managed_updated += 1

        quota_created = 0
        quota_updated = 0
        for item in quota_items:
            if not isinstance(item, dict):
                continue
            if _upsert_namespace_quota(session, item):
                quota_created += 1
            else:
                quota_updated += 1

        templates_updated = _apply_template_bindings(session, template_bindings)
        container_templates_updated = _apply_container_template_bindings(session, container_template_bindings)
        scopes_updated = _apply_namespace_admin_scopes(session, namespace_admin_scopes)

        if dry_run:
            session.rollback()
        else:
            session.commit()

    action = "validated (dry-run)" if dry_run else "applied"
    print(f"namespace backup import {action}:")
    print(f"  managed namespaces: created={managed_created} updated={managed_updated}")
    print(f"  namespace quotas: created={quota_created} updated={quota_updated}")
    print(f"  template namespace bindings updated={templates_updated}")
    print(f"  container template namespace bindings updated={container_templates_updated}")
    print(f"  namespace admin scope bindings updated={scopes_updated}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export or import namespace configuration.")
    sub = parser.add_subparsers(dest="command", required=True)

    export_parser = sub.add_parser("export", help="Export namespace config to JSON")
    export_parser.add_argument("-o", "--output", required=True, help="Output JSON file path")

    import_parser = sub.add_parser("import", help="Import namespace config JSON")
    import_parser.add_argument("-i", "--input", required=True, help="Input JSON file path")
    import_parser.add_argument("--dry-run", action="store_true", help="Validate import without committing changes")

    args = parser.parse_args()
    if args.command == "export":
        export_config(Path(args.output).resolve())
        return
    import_config(Path(args.input).resolve(), dry_run=bool(args.dry_run))


if __name__ == "__main__":
    main()
