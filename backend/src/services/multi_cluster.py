from __future__ import annotations

import re
from dataclasses import dataclass

from sqlmodel import Session, select

from ..config import settings
from ..tables import Cluster, TeamPlacementPolicy
from ..time_utils import utc_now

_TOKEN_RE = re.compile(r"[^a-z0-9._-]+")


class PlacementError(RuntimeError):
    pass


@dataclass
class PlacementDecision:
    cluster_id: str
    reason: str


def _normalize_token(value: str | None, *, default: str = "") -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    normalized = _TOKEN_RE.sub("-", raw).strip("-")
    return normalized or default


def split_csv_tokens(raw: str | None) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for item in str(raw or "").split(","):
        token = _normalize_token(item)
        if not token or token in seen:
            continue
        seen.add(token)
        values.append(token)
    return values


def csv_tokens(values: list[str] | tuple[str, ...] | set[str] | None) -> str:
    normalized = split_csv_tokens(",".join(values or []))
    return ",".join(normalized)


def local_cluster_id() -> str:
    configured = str(getattr(settings, "multi_cluster_local_cluster_id", "") or "").strip()
    return _normalize_token(configured, default="local")


def local_cluster_region() -> str:
    configured = str(getattr(settings, "multi_cluster_local_region", "") or "").strip()
    return _normalize_token(configured, default="local")


def ensure_local_cluster(session: Session) -> Cluster:
    cluster_id = local_cluster_id()
    record = session.get(Cluster, cluster_id)
    if record:
        changed = False
        if not record.is_local:
            record.is_local = True
            changed = True
        if not record.runtime_enabled:
            record.runtime_enabled = True
            changed = True
        if not record.enabled:
            record.enabled = True
            changed = True
        if not record.schedule_enabled:
            record.schedule_enabled = True
            changed = True
        if not str(record.region or "").strip():
            record.region = local_cluster_region()
            changed = True
        if changed:
            record.updated_at = utc_now()
            session.add(record)
            session.commit()
            session.refresh(record)
        return record

    now = utc_now()
    record = Cluster(
        id=cluster_id,
        name="Local Cluster",
        region=local_cluster_region(),
        compliance_tags_csv="",
        capacity_weight=100,
        enabled=True,
        schedule_enabled=True,
        runtime_enabled=True,
        is_local=True,
        kubeconfig="",
        notes="Auto-managed local runtime cluster.",
        health_status="unknown",
        health_message="",
        created_at=now,
        updated_at=now,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def list_clusters_for_scheduling(session: Session) -> list[Cluster]:
    ensure_local_cluster(session)
    rows = session.exec(
        select(Cluster)
        .where(Cluster.enabled == True)  # noqa: E712
        .where(Cluster.schedule_enabled == True)  # noqa: E712
        .where(Cluster.runtime_enabled == True)  # noqa: E712
    ).all()
    if rows:
        return rows
    local = session.get(Cluster, local_cluster_id())
    return [local] if local else []


def _team_policy(session: Session, team: str | None) -> TeamPlacementPolicy | None:
    normalized_team = _normalize_token(team, default="default")
    return session.exec(select(TeamPlacementPolicy).where(TeamPlacementPolicy.team == normalized_team)).first()


def _cluster_compliance_tags(cluster: Cluster) -> set[str]:
    return set(split_csv_tokens(getattr(cluster, "compliance_tags_csv", "")))


def _policy_allows_cluster(cluster: Cluster, policy: TeamPlacementPolicy | None) -> bool:
    if not policy:
        return True
    required_regions = set(split_csv_tokens(getattr(policy, "required_regions_csv", "")))
    required_tags = set(split_csv_tokens(getattr(policy, "required_compliance_tags_csv", "")))
    allowed_cluster_ids = set(split_csv_tokens(getattr(policy, "allowed_cluster_ids_csv", "")))
    if required_regions and _normalize_token(cluster.region) not in required_regions:
        return False
    if required_tags and not required_tags.issubset(_cluster_compliance_tags(cluster)):
        return False
    if allowed_cluster_ids and _normalize_token(cluster.id) not in allowed_cluster_ids:
        return False
    return True


def _cluster_sort_key(cluster: Cluster) -> tuple[int, int, str]:
    weight = max(1, int(getattr(cluster, "capacity_weight", 100) or 100))
    local_bias = 1 if bool(getattr(cluster, "is_local", False)) else 0
    return (weight, local_bias, str(cluster.id or ""))


def select_cluster_for_launch(
    session: Session,
    *,
    team: str | None,
    workload_kind: str,
    template_cluster_id: str | None = None,
) -> PlacementDecision:
    del workload_kind  # Reserved for future workload-kind-specific policies.
    candidates = list_clusters_for_scheduling(session)
    if not candidates:
        raise PlacementError("No runtime-enabled clusters are available for scheduling.")

    policy = _team_policy(session, team)
    filtered = [cluster for cluster in candidates if _policy_allows_cluster(cluster, policy)]
    if not filtered:
        raise PlacementError("No cluster satisfies tenant placement policy constraints.")

    template_cluster = _normalize_token(template_cluster_id)
    if template_cluster:
        narrowed = [cluster for cluster in filtered if _normalize_token(cluster.id) == template_cluster]
        if not narrowed:
            raise PlacementError(
                f"Template requires cluster '{template_cluster}', but it is not currently schedulable for this tenant."
            )
        filtered = narrowed
        return PlacementDecision(cluster_id=filtered[0].id, reason="template-cluster-pin")

    preferred_cluster = _normalize_token(getattr(policy, "preferred_cluster_id", "")) if policy else ""
    hard_pin = bool(getattr(policy, "hard_pin_cluster", False)) if policy else False
    if preferred_cluster:
        preferred = [cluster for cluster in filtered if _normalize_token(cluster.id) == preferred_cluster]
        if preferred:
            return PlacementDecision(cluster_id=preferred[0].id, reason="tenant-preferred-cluster")
        if hard_pin:
            raise PlacementError(
                f"Tenant policy hard-pins cluster '{preferred_cluster}', but it is not currently schedulable."
            )

    chosen = sorted(filtered, key=_cluster_sort_key, reverse=True)[0]
    return PlacementDecision(cluster_id=chosen.id, reason="capacity-weighted")
