import copy
import logging

from kubernetes import client
from kubernetes.client import ApiException

from ..config import settings
from .team_quotas import normalize_team

logger = logging.getLogger(__name__)

_ROLE_NAME = "bretter-backend-runtime"
_ROLE_BINDING_NAME = "bretter-backend-runtime"
_RESOURCE_QUOTA_NAME = "bretter-tenant-quota"
_LIMIT_RANGE_NAME = "bretter-tenant-default-limits"
_DEFAULT_DENY_INGRESS_NAME = "default-deny-ingress"
_DEFAULT_DENY_EGRESS_NAME = "default-deny-egress"
_ALLOW_DNS_EGRESS_NAME = "allow-dns-egress"
_ALLOW_SAME_NS_NAME = "allow-same-namespace-traffic"
_PSA_ENFORCE_KEY = "pod-security.kubernetes.io/enforce"
_PSA_AUDIT_KEY = "pod-security.kubernetes.io/audit"
_PSA_WARN_KEY = "pod-security.kubernetes.io/warn"


def _control_namespace() -> str:
    return str(getattr(settings, "kube_namespace", "labs") or "labs").strip() or "labs"


def _mode() -> str:
    return str(getattr(settings, "team_namespace_mode", "shared") or "shared").strip().lower()


def _bootstrap_enabled() -> bool:
    return bool(getattr(settings, "team_namespace_bootstrap_enabled", True))


def _safe_team_label(team: str | None) -> str:
    return normalize_team(team)


def _pod_security_labels(*, privileged_runtime: bool) -> dict[str, str]:
    if privileged_runtime:
        return {
            _PSA_ENFORCE_KEY: "privileged",
            _PSA_AUDIT_KEY: "restricted",
            _PSA_WARN_KEY: "restricted",
        }
    return {
        _PSA_ENFORCE_KEY: "restricted",
        _PSA_AUDIT_KEY: "restricted",
        _PSA_WARN_KEY: "restricted",
    }


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


def _resource_quota() -> client.V1ResourceQuota:
    return client.V1ResourceQuota(
        metadata=client.V1ObjectMeta(name=_RESOURCE_QUOTA_NAME),
        spec=client.V1ResourceQuotaSpec(
            hard={
                "pods": "200",
                "services": "100",
                "persistentvolumeclaims": "200",
                "requests.cpu": "8",
                "limits.cpu": "16",
                "requests.memory": "16Gi",
                "limits.memory": "32Gi",
                "requests.storage": "2Ti",
            }
        ),
    )


def _limit_range() -> client.V1LimitRange:
    return client.V1LimitRange(
        metadata=client.V1ObjectMeta(name=_LIMIT_RANGE_NAME),
        spec=client.V1LimitRangeSpec(
            limits=[
                client.V1LimitRangeItem(
                    type="Container",
                    min={"cpu": "50m", "memory": "64Mi"},
                    default_request={"cpu": "250m", "memory": "256Mi"},
                    default={"cpu": "2", "memory": "2Gi"},
                    max={"cpu": "8", "memory": "16Gi"},
                )
            ]
        ),
    )


def _default_network_policies() -> list[client.V1NetworkPolicy]:
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
    ]


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
        subjects=[client.V1Subject(kind="ServiceAccount", name="bretter-backend", namespace=service_account_namespace)],
    )
    try:
        rbac_api.create_namespaced_role_binding(namespace=namespace, body=body)
    except ApiException as exc:
        if exc.status != 409:
            raise
        rbac_api.patch_namespaced_role_binding(name=_ROLE_BINDING_NAME, namespace=namespace, body=body)


def _upsert_resource_quota(core_api: client.CoreV1Api, namespace: str) -> None:
    body = _resource_quota()
    try:
        core_api.create_namespaced_resource_quota(namespace=namespace, body=body)
    except ApiException as exc:
        if exc.status != 409:
            raise
        core_api.patch_namespaced_resource_quota(name=_RESOURCE_QUOTA_NAME, namespace=namespace, body=body)


def _upsert_limit_range(core_api: client.CoreV1Api, namespace: str) -> None:
    body = _limit_range()
    try:
        core_api.create_namespaced_limit_range(namespace=namespace, body=body)
    except ApiException as exc:
        if exc.status != 409:
            raise
        core_api.patch_namespaced_limit_range(name=_LIMIT_RANGE_NAME, namespace=namespace, body=body)


def _upsert_network_policies(networking_api: client.NetworkingV1Api, namespace: str) -> None:
    for policy in _default_network_policies():
        name = str(policy.metadata.name)
        try:
            networking_api.create_namespaced_network_policy(namespace=namespace, body=policy)
        except ApiException as exc:
            if exc.status != 409:
                raise
            networking_api.patch_namespaced_network_policy(name=name, namespace=namespace, body=policy)


def _upsert_namespace(
    core_api: client.CoreV1Api,
    namespace: str,
    team: str | None,
    *,
    privileged_runtime: bool,
) -> None:
    labels = {
        "app.kubernetes.io/part-of": "bretter-labs",
        "labs.bretter.io/tenant": "true",
        "labs.bretter.io/team": _safe_team_label(team),
        "labs.bretter.io/runtime-profile": "vm-privileged" if privileged_runtime else "restricted",
    }
    labels.update(_pod_security_labels(privileged_runtime=privileged_runtime))
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
) -> None:
    if not _bootstrap_enabled():
        return
    if _mode() != "per_team":
        return
    target_namespace = str(namespace or "").strip()
    if not target_namespace:
        return
    control_namespace = _control_namespace()
    if target_namespace == control_namespace:
        return
    core_api = kube_service._client()
    networking_api = kube_service._networking_client()
    rbac_api = client.RbacAuthorizationV1Api(core_api.api_client)
    try:
        _upsert_namespace(core_api, target_namespace, team, privileged_runtime=privileged_runtime)
        _upsert_role(rbac_api, target_namespace)
        _upsert_role_binding(rbac_api, target_namespace, service_account_namespace=control_namespace)
        _upsert_resource_quota(core_api, target_namespace)
        _upsert_limit_range(core_api, target_namespace)
        _upsert_network_policies(networking_api, target_namespace)
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
        _sync_configmap(
            core_api,
            source_namespace=control_namespace,
            target_namespace=target_namespace,
            name=str(getattr(settings, "kube_spice_embed_configmap", "") or ""),
        )
    except ApiException as exc:
        detail = exc.reason or str(exc.status)
        raise RuntimeError(f"failed to bootstrap tenant runtime namespace {target_namespace}: {detail}") from exc
