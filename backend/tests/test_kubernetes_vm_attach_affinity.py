from src.services.kubernetes import KubernetesService, PodRequest


class _FakeCore:
    def __init__(self):
        self.created: list[tuple[str, object]] = []

    def create_namespaced_pod(self, namespace: str, body):
        self.created.append((namespace, body))


def _pod_request() -> PodRequest:
    return PodRequest(
        instance_id="caabc8c2-6f1b-4952-9c1a-a4a6926b8038",
        template_id="tmpl-test",
        image_path="windows-11.qcow2",
        image_source_pvc="img-src-c4073787",
        os_type="windows",
        cpu_cores=4,
        ram_mb=8192,
        owner="admin",
        network_mode="unrestricted",
        namespace="labs",
    )


def test_create_pod_prefers_attached_volume_node(monkeypatch):
    core = _FakeCore()
    svc = KubernetesService(core_api=core, networking_api=None, namespace_override="labs")
    monkeypatch.setattr(svc, "ensure_namespace", lambda _namespace: None)
    monkeypatch.setattr(svc, "_ensure_instance_disk_pvc", lambda _req: "pool-3d0171ab-f8f829")
    monkeypatch.setattr(svc, "_attached_node_for_pvc", lambda _claim, namespace=None: "cbekube2")

    status = svc.create_pod(_pod_request())

    assert status.disk_pvc == "pool-3d0171ab-f8f829"
    assert len(core.created) == 1
    _namespace, body = core.created[0]
    affinity = body.spec.affinity
    assert affinity is not None
    assert affinity.node_affinity is not None
    preferred_terms = affinity.node_affinity.preferred_during_scheduling_ignored_during_execution or []
    assert len(preferred_terms) == 1
    term = preferred_terms[0]
    assert term.weight == 100
    expressions = term.preference.match_expressions or []
    assert len(expressions) == 1
    assert expressions[0].key == "kubernetes.io/hostname"
    assert expressions[0].operator == "In"
    assert expressions[0].values == ["cbekube2"]


def test_create_pod_without_attached_node_has_no_node_affinity(monkeypatch):
    core = _FakeCore()
    svc = KubernetesService(core_api=core, networking_api=None, namespace_override="labs")
    monkeypatch.setattr(svc, "ensure_namespace", lambda _namespace: None)
    monkeypatch.setattr(svc, "_ensure_instance_disk_pvc", lambda _req: "pool-3d0171ab-f8f829")
    monkeypatch.setattr(svc, "_attached_node_for_pvc", lambda _claim, namespace=None: None)

    svc.create_pod(_pod_request())

    assert len(core.created) == 1
    _namespace, body = core.created[0]
    affinity = body.spec.affinity
    if affinity is None:
        assert affinity is None
    else:
        assert affinity.node_affinity is None
