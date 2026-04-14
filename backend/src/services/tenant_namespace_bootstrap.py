import copy
from dataclasses import dataclass, replace
import logging

from kubernetes import client
from kubernetes.client import ApiException
from kubernetes.utils import parse_quantity
from sqlmodel import select

from ..config import settings
from ..db import session_scope
from ..tables import ManagedNamespace
from ..time_utils import utc_now
from .team_quotas import normalize_namespace, normalize_team

logger = logging.getLogger(__name__)

_ROLE_NAME = "bretter-backend-runtime"
_ROLE_BINDING_NAME = "bretter-backend-runtime"
_RESOURCE_QUOTA_NAME = "bretter-tenant-quota"
_LIMIT_RANGE_NAME = "bretter-tenant-default-limits"
_DEFAULT_DENY_INGRESS_NAME = "default-deny-ingress"
_DEFAULT_DENY_EGRESS_NAME = "default-deny-egress"
_ALLOW_DNS_EGRESS_NAME = "allow-dns-egress"
_ALLOW_SAME_NS_NAME = "allow-same-namespace-traffic"
_ALLOW_CONTROL_PLANE_INGRESS_NAME = "allow-control-plane-ingress"
_PSA_ENFORCE_KEY = "pod-security.kubernetes.io/enforce"
_PSA_AUDIT_KEY = "pod-security.kubernetes.io/audit"
_PSA_WARN_KEY = "pod-security.kubernetes.io/warn"
_VALID_SECURITY_PROFILES = {"restricted", "baseline", "privileged"}


@dataclass(frozen=True)
class NamespaceBootstrapPolicy:
    team_label: str = "default"
    security_profile: str = "baseline"
    enforce_network_policies: bool = True
    max_pods: str = "200"
    max_services: str = "100"
    max_persistent_volume_claims: str = "200"
    requests_cpu: str = "8"
    limits_cpu: str = "16"
    requests_memory: str = "16Gi"
    limits_memory: str = "32Gi"
    requests_storage: str = "2Ti"
    limit_min_cpu: str = "50m"
    limit_min_memory: str = "64Mi"
    limit_default_request_cpu: str = "250m"
    limit_default_request_memory: str = "256Mi"
    limit_default_cpu: str = "2"
    limit_default_memory: str = "2Gi"
    limit_max_cpu: str = "8"
    limit_max_memory: str = "16Gi"


@dataclass(frozen=True)
class NamespaceReconcileResult:
    namespace: str
    ok: bool
    detail: str = ""


def _control_namespace() -> str:
    return str(getattr(settings, "kube_namespace", "labs") or "labs").strip() or "labs"


def _mode() -> str:
    return str(getattr(settings, "team_namespace_mode", "shared") or "shared").strip().lower()


def _bootstrap_enabled() -> bool:
    return bool(getattr(settings, "team_namespace_bootstrap_enabled", True))


def _safe_team_label(team: str | None) -> str:
    return normalize_team(team)


def _normalize_security_profile(value: str | None) -> str:
    profile = str(value or "").strip().lower()
    if profile in _VALID_SECURITY_PROFILES:
        return profile
    return "baseline"


def _default_policy(*, privileged_runtime: bool) -> NamespaceBootstrapPolicy:
    profile = "privileged" if privileged_runtime else "baseline"
    return NamespaceBootstrapPolicy(team_label="default", security_profile=profile)


def _policy_from_managed_namespace(row: ManagedNamespace) -> NamespaceBootstrapPolicy:
    return NamespaceBootstrapPolicy(
        team_label=normalize_team(getattr(row, "team_label", None)),
        security_profile=_normalize_security_profile(getattr(row, "security_profile", None)),
        enforce_network_policies=bool(getattr(row, "enforce_network_policies", True)),
        max_pods=str(getattr(row, "max_pods", "") or "200").strip(),
        max_services=str(getattr(row, "max_services", "") or "100").strip(),
        max_persistent_volume_claims=str(getattr(row, "max_persistent_volume_claims", "") or "200").strip(),
        requests_cpu=str(getattr(row, "requests_cpu", "") or "8").strip(),
        limits_cpu=str(getattr(row, "limits_cpu", "") or "16").strip(),
        requests_memory=str(getattr(row, "requests_memory", "") or "16Gi").strip(),
        limits_memory=str(getattr(row, "limits_memory", "") or "32Gi").strip(),
        requests_storage=str(getattr(row, "requests_storage", "") or "2Ti").strip(),
        limit_min_cpu=str(getattr(row, "limit_min_cpu", "") or "50m").strip(),
        limit_min_memory=str(getattr(row, "limit_min_memory", "") or "64Mi").strip(),
        limit_default_request_cpu=str(getattr(row, "limit_default_request_cpu", "") or "250m").strip(),
        limit_default_request_memory=str(getattr(row, "limit_default_request_memory", "") or "256Mi").strip(),
        limit_default_cpu=str(getattr(row, "limit_default_cpu", "") or "2").strip(),
        limit_default_memory=str(getattr(row, "limit_default_memory", "") or "2Gi").strip(),
        limit_max_cpu=str(getattr(row, "limit_max_cpu", "") or "8").strip(),
        limit_max_memory=str(getattr(row, "limit_max_memory", "") or "16Gi").strip(),
    )


def managed_namespace_policy(row: ManagedNamespace) -> NamespaceBootstrapPolicy:
    return _policy_from_managed_namespace(row)


def _managed_namespace_policy(namespace: str) -> NamespaceBootstrapPolicy | None:
    target_namespace = normalize_namespace(namespace)
    try:
        with session_scope() as session:
            row = session.exec(
                select(ManagedNamespace)
                .where(ManagedNamespace.namespace == target_namespace)
                .where(ManagedNamespace.enabled == True)  # noqa: E712
            ).first()
            if not row:
                return None
            return _policy_from_managed_namespace(row)
    except Exception as exc:
        logger.warning("Failed reading managed namespace policy for %s: %s", target_namespace, exc)
        return None


def _pod_security_labels(*, security_profile: str) -> dict[str, str]:
    profile = _normalize_security_profile(security_profile)
    if profile == "privileged":
        return {
            _PSA_ENFORCE_KEY: "privileged",
            _PSA_AUDIT_KEY: "restricted",
            _PSA_WARN_KEY: "restricted",
        }
    return {_PSA_ENFORCE_KEY: profile, _PSA_AUDIT_KEY: "restricted", _PSA_WARN_KEY: "restricted"}


def _rbac_service_account_subject(service_account_namespace: str):
    subject_cls = getattr(client, "RbacV1Subject", None) or getattr(client, "V1Subject", None)
    if subject_cls is not None:
        return subject_cls(kind="ServiceAccount", name="bretter-backend", namespace=service_account_namespace)
    return {"kind": "ServiceAccount", "name": "bretter-backend", "namespace": service_account_namespace}


def _runtime_role() -> client.V1Role:
    return client.V1Role(
        metadata=client.V1ObjectMeta(name=_ROLE_NAME),
        rules=[
            client.V1PolicyRule(
                api_groups=[""],
                resources=["pods", "pods/log", "pods/exec", "services", "persistentvolumeclaims", "events"],
                verbs=["get", "list", "watch", "create", "update", "patch", "delete"],
            ),
            client.V1PolicyRule(
                api_groups=["networking.k8s.io"],
                resources=["networkpolicies"],
                verbs=["get", "list", "watch", "create", "update", "patch", "delete"],
            ),
            client.V1PolicyRule(
                api_groups=["cdi.kubevirt.io"],
                resources=["datavolumes"],
                verbs=["get", "list", "watch", "create", "update", "patch", "delete"],
            ),
            client.V1PolicyRule(
                api_groups=["upload.cdi.kubevirt.io"],
                resources=["uploadtokenrequests"],
                verbs=["get", "list", "watch", "create", "delete"],
            ),
            client.V1PolicyRule(
                api_groups=[""],
                resources=["configmaps", "secrets"],
                verbs=["get", "list", "watch", "create", "update", "patch"],
            ),
            client.V1PolicyRule(
                api_groups=[""],
                resources=["resourcequotas", "limitranges"],
                verbs=["get", "list", "watch", "create", "update", "patch"],
            ),
        ],
    )


def _resource_quota(policy: NamespaceBootstrapPolicy) -> client.V1ResourceQuota:
    return client.V1ResourceQuota(
        metadata=client.V1ObjectMeta(name=_RESOURCE_QUOTA_NAME),
        spec=client.V1ResourceQuotaSpec(
            hard={
                "pods": str(policy.max_pods or "200"),
                "services": str(policy.max_services or "100"),
                "persistentvolumeclaims": str(policy.max_persistent_volume_claims or "200"),
                "requests.cpu": str(policy.requests_cpu or "8"),
                "limits.cpu": str(policy.limits_cpu or "16"),
                "requests.memory": str(policy.requests_memory or "16Gi"),
                "limits.memory": str(policy.limits_memory or "32Gi"),
                "requests.storage": str(policy.requests_storage or "2Ti"),
            }
        ),
    )


def _limit_range(policy: NamespaceBootstrapPolicy) -> client.V1LimitRange:
    return client.V1LimitRange(
        metadata=client.V1ObjectMeta(name=_LIMIT_RANGE_NAME),
        spec=client.V1LimitRangeSpec(
            limits=[
                client.V1LimitRangeItem(
                    type="Container",
                    min={"cpu": str(policy.limit_min_cpu or "50m"), "memory": str(policy.limit_min_memory or "64Mi")},
                    default_request={
                        "cpu": str(policy.limit_default_request_cpu or "250m"),
                        "memory": str(policy.limit_default_request_memory or "256Mi"),
                    },
                    default={
                        "cpu": str(policy.limit_default_cpu or "2"),
                        "memory": str(policy.limit_default_memory or "2Gi"),
                    },
                    max={"cpu": str(policy.limit_max_cpu or "8"), "memory": str(policy.limit_max_memory or "16Gi")},
                )
            ]
        ),
    )


def _default_network_policies(control_namespace: str) -> list[client.V1NetworkPolicy]:
    control_ns = normalize_namespace(control_namespace) or "labs"
    return [
        client.V1NetworkPolicy(
            metadata=client.V1ObjectMeta(name=_DEFAULT_DENY_INGRESS_NAME),
            spec=client.V1NetworkPolicySpec(
                pod_selector=client.V1LabelSelector(match_labels={}),
                policy_types=["Ingress"],
                ingress=[],
            ),
        ),
        client.V1NetworkPolicy(
            metadata=client.V1ObjectMeta(name=_DEFAULT_DENY_EGRESS_NAME),
            spec=client.V1NetworkPolicySpec(
                pod_selector=client.V1LabelSelector(match_labels={}),
                policy_types=["Egress"],
                egress=[],
            ),
        ),
        client.V1NetworkPolicy(
            metadata=client.V1ObjectMeta(name=_ALLOW_DNS_EGRESS_NAME),
            spec=client.V1NetworkPolicySpec(
                pod_selector=client.V1LabelSelector(match_labels={}),
                policy_types=["Egress"],
                egress=[
                    client.V1NetworkPolicyEgressRule(
                        to=[
                            client.V1NetworkPolicyPeer(
                                namespace_selector=client.V1LabelSelector(
                                    match_labels={"kubernetes.io/metadata.name": "kube-system"}
                                )
                            )
                        ],
                        ports=[
                            client.V1NetworkPolicyPort(protocol="UDP", port=53),
                            client.V1NetworkPolicyPort(protocol="TCP", port=53),
                        ],
                    )
                ],
            ),
        ),
        client.V1NetworkPolicy(
            metadata=client.V1ObjectMeta(name=_ALLOW_SAME_NS_NAME),
            spec=client.V1NetworkPolicySpec(
                pod_selector=client.V1LabelSelector(match_labels={}),
                policy_types=["Ingress", "Egress"],
                ingress=[
                    client.V1NetworkPolicyIngressRule(
                        _from=[client.V1NetworkPolicyPeer(pod_selector=client.V1LabelSelector(match_labels={}))]
                    )
                ],
                egress=[
                    client.V1NetworkPolicyEgressRule(
                        to=[client.V1NetworkPolicyPeer(pod_selector=client.V1LabelSelector(match_labels={}))]
                    )
                ],
            ),
        ),
        client.V1NetworkPolicy(
            metadata=client.V1ObjectMeta(name=_ALLOW_CONTROL_PLANE_INGRESS_NAME),
            spec=client.V1NetworkPolicySpec(
                pod_selector=client.V1LabelSelector(match_labels={}),
                policy_types=["Ingress"],
                ingress=[
                    client.V1NetworkPolicyIngressRule(
                        _from=[
                            client.V1NetworkPolicyPeer(
                                namespace_selector=client.V1LabelSelector(
                                    match_labels={"kubernetes.io/metadata.name": control_ns}
                                ),
                                pod_selector=client.V1LabelSelector(match_labels={"app": "bretter-backend"}),
                            )
                        ]
                    )
                ],
            ),
        ),
    ]


def _quantity_matches(expected: str, actual: str) -> bool:
    expected_raw = str(expected or "").strip()
    actual_raw = str(actual or "").strip()
    if expected_raw == actual_raw:
        return True
    if not expected_raw or not actual_raw:
        return False
    try:
        return int(parse_quantity(expected_raw)) == int(parse_quantity(actual_raw))
    except Exception:
        return expected_raw == actual_raw


def _expected_namespace_labels(team_label: str, policy: NamespaceBootstrapPolicy) -> dict[str, str]:
    resolved_profile = _normalize_security_profile(policy.security_profile)
    runtime_profile_label = "vm-privileged" if resolved_profile == "privileged" else resolved_profile
    labels = {
        "app.kubernetes.io/part-of": "bretter-labs",
        "labs.bretter.io/tenant": "true",
        "labs.bretter.io/team": _safe_team_label(team_label),
        "labs.bretter.io/runtime-profile": runtime_profile_label,
    }
    labels.update(_pod_security_labels(security_profile=resolved_profile))
    return labels


def detect_namespace_policy_drift(
    kube_service,
    *,
    namespace: str,
    team_label: str,
    policy: NamespaceBootstrapPolicy,
) -> list[str]:
    drifts: list[str] = []
    target_namespace = normalize_namespace(namespace)
    if not target_namespace:
        return ["namespace name is empty"]

    control_namespace = _control_namespace()
    core_api = kube_service._client()
    networking_api = kube_service._networking_client()
    rbac_api = client.RbacAuthorizationV1Api(core_api.api_client)

    expected_labels = _expected_namespace_labels(team_label=team_label, policy=policy)
    try:
        namespace_row = core_api.read_namespace(name=target_namespace)
        live_labels = dict(getattr(namespace_row.metadata, "labels", {}) or {})
        for key, expected in expected_labels.items():
            actual = str(live_labels.get(key, "") or "").strip()
            if actual != expected:
                drifts.append(f"namespace label {key} drift (expected={expected!r}, actual={actual!r})")
    except ApiException as exc:
        if exc.status == 404:
            drifts.append("namespace is missing")
        else:
            drifts.append(f"namespace read failed ({exc.status} {exc.reason})")
        return drifts
    except Exception as exc:
        drifts.append(f"namespace read failed ({exc})")
        return drifts

    try:
        rbac_api.read_namespaced_role(name=_ROLE_NAME, namespace=target_namespace)
    except ApiException as exc:
        if exc.status == 404:
            drifts.append("runtime role is missing")
        else:
            drifts.append(f"runtime role read failed ({exc.status} {exc.reason})")
    except Exception as exc:
        drifts.append(f"runtime role read failed ({exc})")

    try:
        binding = rbac_api.read_namespaced_role_binding(name=_ROLE_BINDING_NAME, namespace=target_namespace)
        subjects = list(getattr(binding, "subjects", None) or [])
        expected_subject = ("ServiceAccount", "bretter-backend", control_namespace)
        matched = False
        for subject in subjects:
            if isinstance(subject, dict):
                kind = str(subject.get("kind", "")).strip()
                name = str(subject.get("name", "")).strip()
                subject_namespace = str(subject.get("namespace", "")).strip()
            else:
                kind = str(getattr(subject, "kind", "") or "").strip()
                name = str(getattr(subject, "name", "") or "").strip()
                subject_namespace = str(getattr(subject, "namespace", "") or "").strip()
            if (kind, name, subject_namespace) == expected_subject:
                matched = True
                break
        if not matched:
            drifts.append("runtime role binding subjects drift")
    except ApiException as exc:
        if exc.status == 404:
            drifts.append("runtime role binding is missing")
        else:
            drifts.append(f"runtime role binding read failed ({exc.status} {exc.reason})")
    except Exception as exc:
        drifts.append(f"runtime role binding read failed ({exc})")

    expected_quota = _resource_quota(policy).spec.hard or {}
    try:
        quota = core_api.read_namespaced_resource_quota(name=_RESOURCE_QUOTA_NAME, namespace=target_namespace)
        live_hard = dict(getattr(getattr(quota, "spec", None), "hard", {}) or {})
        for key, expected in expected_quota.items():
            actual = str(live_hard.get(key, "") or "").strip()
            if not _quantity_matches(str(expected), actual):
                drifts.append(f"resource quota {key} drift (expected={expected!r}, actual={actual!r})")
    except ApiException as exc:
        if exc.status == 404:
            drifts.append("resource quota is missing")
        else:
            drifts.append(f"resource quota read failed ({exc.status} {exc.reason})")
    except Exception as exc:
        drifts.append(f"resource quota read failed ({exc})")

    expected_limit = _limit_range(policy)
    expected_limits = list(getattr(getattr(expected_limit, "spec", None), "limits", None) or [])
    expected_container_limit = next(
        (row for row in expected_limits if str(getattr(row, "type", "")) == "Container"), None
    )
    try:
        live_limit = core_api.read_namespaced_limit_range(name=_LIMIT_RANGE_NAME, namespace=target_namespace)
        live_limits = list(getattr(getattr(live_limit, "spec", None), "limits", None) or [])
        live_container_limit = next((row for row in live_limits if str(getattr(row, "type", "")) == "Container"), None)
        if expected_container_limit is None or live_container_limit is None:
            drifts.append("limit range container defaults drift")
        else:
            for section_name in ("min", "default_request", "default", "max"):
                expected_section = dict(getattr(expected_container_limit, section_name, {}) or {})
                live_section = dict(getattr(live_container_limit, section_name, {}) or {})
                for key, expected in expected_section.items():
                    actual = str(live_section.get(key, "") or "").strip()
                    if not _quantity_matches(str(expected), actual):
                        drifts.append(
                            f"limit range {section_name}.{key} drift (expected={expected!r}, actual={actual!r})"
                        )
    except ApiException as exc:
        if exc.status == 404:
            drifts.append("limit range is missing")
        else:
            drifts.append(f"limit range read failed ({exc.status} {exc.reason})")
    except Exception as exc:
        drifts.append(f"limit range read failed ({exc})")

    policy_names = {str(item.metadata.name or "").strip() for item in _default_network_policies(control_namespace)}
    try:
        live_names = {
            str(getattr(getattr(item, "metadata", None), "name", "") or "").strip()
            for item in networking_api.list_namespaced_network_policy(namespace=target_namespace).items
        }
        if policy.enforce_network_policies:
            missing = sorted(name for name in policy_names if name and name not in live_names)
            if missing:
                drifts.append(f"network policies missing: {', '.join(missing)}")
        else:
            unexpected = sorted(name for name in policy_names if name and name in live_names)
            if unexpected:
                drifts.append(f"network policies should be absent: {', '.join(unexpected)}")
    except ApiException as exc:
        if exc.status == 404 and policy.enforce_network_policies:
            drifts.append("network policies list unavailable")
        elif exc.status != 404:
            drifts.append(f"network policy read failed ({exc.status} {exc.reason})")
    except Exception as exc:
        drifts.append(f"network policy read failed ({exc})")

    return drifts


def _upsert_role(rbac_api: client.RbacAuthorizationV1Api, namespace: str) -> None:
    body = _runtime_role()
    try:
        rbac_api.create_namespaced_role(namespace=namespace, body=body)
    except ApiException as exc:
        if exc.status != 409:
            raise
        rbac_api.patch_namespaced_role(name=_ROLE_NAME, namespace=namespace, body=body)


def _upsert_role_binding(
    rbac_api: client.RbacAuthorizationV1Api,
    namespace: str,
    *,
    service_account_namespace: str,
) -> None:
    body = client.V1RoleBinding(
        metadata=client.V1ObjectMeta(name=_ROLE_BINDING_NAME),
        role_ref=client.V1RoleRef(api_group="rbac.authorization.k8s.io", kind="Role", name=_ROLE_NAME),
        subjects=[_rbac_service_account_subject(service_account_namespace)],
    )
    try:
        rbac_api.create_namespaced_role_binding(namespace=namespace, body=body)
    except ApiException as exc:
        if exc.status != 409:
            raise
        rbac_api.patch_namespaced_role_binding(name=_ROLE_BINDING_NAME, namespace=namespace, body=body)


def _upsert_resource_quota(core_api: client.CoreV1Api, namespace: str, policy: NamespaceBootstrapPolicy) -> None:
    body = _resource_quota(policy)
    try:
        core_api.create_namespaced_resource_quota(namespace=namespace, body=body)
    except ApiException as exc:
        if exc.status != 409:
            raise
        core_api.patch_namespaced_resource_quota(name=_RESOURCE_QUOTA_NAME, namespace=namespace, body=body)


def _upsert_limit_range(core_api: client.CoreV1Api, namespace: str, policy: NamespaceBootstrapPolicy) -> None:
    body = _limit_range(policy)
    try:
        core_api.create_namespaced_limit_range(namespace=namespace, body=body)
    except ApiException as exc:
        if exc.status != 409:
            raise
        core_api.patch_namespaced_limit_range(name=_LIMIT_RANGE_NAME, namespace=namespace, body=body)


def _upsert_network_policies(
    networking_api: client.NetworkingV1Api, namespace: str, policy: NamespaceBootstrapPolicy
) -> None:
    if not bool(policy.enforce_network_policies):
        for name in (
            _DEFAULT_DENY_INGRESS_NAME,
            _DEFAULT_DENY_EGRESS_NAME,
            _ALLOW_DNS_EGRESS_NAME,
            _ALLOW_SAME_NS_NAME,
            _ALLOW_CONTROL_PLANE_INGRESS_NAME,
        ):
            try:
                networking_api.delete_namespaced_network_policy(name=name, namespace=namespace)
            except ApiException as exc:
                if exc.status != 404:
                    raise
        return
    for netpol in _default_network_policies(_control_namespace()):
        name = str(netpol.metadata.name)
        try:
            networking_api.create_namespaced_network_policy(namespace=namespace, body=netpol)
        except ApiException as exc:
            if exc.status != 409:
                raise
            networking_api.patch_namespaced_network_policy(name=name, namespace=namespace, body=netpol)


def _upsert_namespace(
    core_api: client.CoreV1Api,
    namespace: str,
    team_label: str | None,
    *,
    policy: NamespaceBootstrapPolicy,
) -> None:
    resolved_profile = _normalize_security_profile(policy.security_profile)
    runtime_profile_label = "vm-privileged" if resolved_profile == "privileged" else resolved_profile
    labels = {
        "app.kubernetes.io/part-of": "bretter-labs",
        "labs.bretter.io/tenant": "true",
        "labs.bretter.io/team": _safe_team_label(team_label),
        "labs.bretter.io/runtime-profile": runtime_profile_label,
    }
    labels.update(_pod_security_labels(security_profile=resolved_profile))
    body = client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace, labels=labels))
    try:
        core_api.create_namespace(body=body)
    except ApiException as exc:
        if exc.status != 409:
            raise
        core_api.patch_namespace(name=namespace, body=body)


def _sync_secret(core_api: client.CoreV1Api, *, source_namespace: str, target_namespace: str, name: str) -> None:
    secret_name = str(name or "").strip()
    if not secret_name:
        return
    try:
        source = core_api.read_namespaced_secret(name=secret_name, namespace=source_namespace)
    except ApiException as exc:
        if exc.status == 404:
            logger.warning("Tenant bootstrap skipped missing source secret %s/%s", source_namespace, secret_name)
            return
        raise
    target = client.V1Secret(
        metadata=client.V1ObjectMeta(name=secret_name, labels={"app.kubernetes.io/part-of": "bretter-labs"}),
        type=source.type,
        data=copy.deepcopy(source.data or {}),
        string_data=copy.deepcopy(source.string_data or {}),
        immutable=source.immutable,
    )
    try:
        core_api.create_namespaced_secret(namespace=target_namespace, body=target)
    except ApiException as exc:
        if exc.status != 409:
            raise
        core_api.patch_namespaced_secret(name=secret_name, namespace=target_namespace, body=target)


def _sync_configmap(core_api: client.CoreV1Api, *, source_namespace: str, target_namespace: str, name: str) -> None:
    configmap_name = str(name or "").strip()
    if not configmap_name:
        return
    try:
        source = core_api.read_namespaced_config_map(name=configmap_name, namespace=source_namespace)
    except ApiException as exc:
        if exc.status == 404:
            logger.warning("Tenant bootstrap skipped missing source ConfigMap %s/%s", source_namespace, configmap_name)
            return
        raise
    target = client.V1ConfigMap(
        metadata=client.V1ObjectMeta(name=configmap_name, labels={"app.kubernetes.io/part-of": "bretter-labs"}),
        data=copy.deepcopy(source.data or {}),
        binary_data=copy.deepcopy(source.binary_data or {}),
        immutable=source.immutable,
    )
    try:
        core_api.create_namespaced_config_map(namespace=target_namespace, body=target)
    except ApiException as exc:
        if exc.status != 409:
            raise
        core_api.patch_namespaced_config_map(name=configmap_name, namespace=target_namespace, body=target)


def ensure_team_runtime_namespace(
    kube_service,
    *,
    team: str | None,
    namespace: str,
    privileged_runtime: bool = False,
    policy: NamespaceBootstrapPolicy | None = None,
    enforce_per_team_mode: bool = True,
) -> None:
    if not _bootstrap_enabled():
        return
    if enforce_per_team_mode and _mode() != "per_team":
        return
    target_namespace = str(namespace or "").strip()
    if not target_namespace:
        return
    control_namespace = _control_namespace()
    if target_namespace == control_namespace:
        return
    effective_policy = (
        policy or _managed_namespace_policy(target_namespace) or _default_policy(privileged_runtime=privileged_runtime)
    )
    effective_policy = replace(
        effective_policy,
        security_profile=(
            "privileged" if privileged_runtime else _normalize_security_profile(effective_policy.security_profile)
        ),
    )
    team_label = normalize_team(team) if str(team or "").strip() else normalize_team(effective_policy.team_label)
    core_api = kube_service._client()
    networking_api = kube_service._networking_client()
    rbac_api = client.RbacAuthorizationV1Api(core_api.api_client)
    try:
        _upsert_namespace(core_api, target_namespace, team_label, policy=effective_policy)
        _upsert_role(rbac_api, target_namespace)
        _upsert_role_binding(rbac_api, target_namespace, service_account_namespace=control_namespace)
        _upsert_resource_quota(core_api, target_namespace, effective_policy)
        _upsert_limit_range(core_api, target_namespace, effective_policy)
        _upsert_network_policies(networking_api, target_namespace, effective_policy)
        _sync_secret(
            core_api,
            source_namespace=control_namespace,
            target_namespace=target_namespace,
            name=str(getattr(settings, "image_pull_secret", "") or ""),
        )
        _sync_secret(
            core_api,
            source_namespace=control_namespace,
            target_namespace=target_namespace,
            name=str(getattr(settings, "kube_tls_secret", "") or ""),
        )
        _sync_secret(
            core_api,
            source_namespace=control_namespace,
            target_namespace=target_namespace,
            name=str(getattr(settings, "runtime_secrets_secret_name", "") or ""),
        )
        _sync_secret(
            core_api,
            source_namespace=control_namespace,
            target_namespace=target_namespace,
            name=str(getattr(settings, "container_signature_key_secret_name", "") or ""),
        )
        _sync_configmap(
            core_api,
            source_namespace=control_namespace,
            target_namespace=target_namespace,
            name=str(getattr(settings, "kube_spice_embed_configmap", "") or ""),
        )
    except ApiException as exc:
        detail = exc.reason or str(exc.status)
        raise RuntimeError(f"failed to bootstrap tenant runtime namespace {target_namespace}: {detail}") from exc


def reconcile_managed_namespace(kube_service, row: ManagedNamespace) -> None:
    policy = _policy_from_managed_namespace(row)
    privileged_runtime = str(policy.security_profile or "").strip().lower() == "privileged"
    ensure_team_runtime_namespace(
        kube_service,
        team=getattr(row, "team_label", "default"),
        namespace=str(getattr(row, "namespace", "") or ""),
        privileged_runtime=privileged_runtime,
        policy=policy,
        enforce_per_team_mode=False,
    )


def reconcile_all_managed_namespaces(kube_service) -> list[NamespaceReconcileResult]:
    results: list[NamespaceReconcileResult] = []
    control_namespace = _control_namespace()
    with session_scope() as session:
        rows = session.exec(select(ManagedNamespace).where(ManagedNamespace.enabled == True)).all()  # noqa: E712
        for row in rows:
            namespace = normalize_namespace(getattr(row, "namespace", None))
            if not namespace:
                continue
            if namespace == control_namespace:
                row.last_reconciled_at = utc_now()
                row.updated_at = row.last_reconciled_at
                session.add(row)
                results.append(
                    NamespaceReconcileResult(
                        namespace=namespace,
                        ok=True,
                        detail="skipped drift enforcement for control namespace",
                    )
                )
                continue
            policy = _policy_from_managed_namespace(row)
            team_label = normalize_team(getattr(row, "team_label", "default"))
            try:
                before_drift = detect_namespace_policy_drift(
                    kube_service,
                    namespace=namespace,
                    team_label=team_label,
                    policy=policy,
                )
                reconcile_managed_namespace(kube_service, row)
                after_drift = detect_namespace_policy_drift(
                    kube_service,
                    namespace=namespace,
                    team_label=team_label,
                    policy=policy,
                )
                if after_drift:
                    results.append(
                        NamespaceReconcileResult(
                            namespace=namespace,
                            ok=False,
                            detail=f"drift remains after reconcile: {', '.join(after_drift[:5])}",
                        )
                    )
                    continue
                row.last_reconciled_at = utc_now()
                row.updated_at = row.last_reconciled_at
                session.add(row)
                detail = "reconciled"
                if before_drift:
                    detail = f"reconciled {len(before_drift)} drift item(s)"
                results.append(NamespaceReconcileResult(namespace=namespace, ok=True, detail=detail))
            except Exception as exc:
                results.append(NamespaceReconcileResult(namespace=namespace, ok=False, detail=str(exc)))
        session.commit()
    return results
