from sqlmodel import Session

from src.db import engine
from src.tables import ContainerImage, ContainerTemplate, Image, Template
from src.time_utils import utc_now
import src.routes.user as user_routes
import src.routes.user_containers as user_container_routes


def _seed_vm_remote_template() -> None:
    with Session(engine) as session:
        if not session.get(Image, "img-remote-vm-1"):
            session.add(
                Image(
                    id="img-remote-vm-1",
                    name="Remote VM Image",
                    filename="remote-win11.vdi",
                    tenant="global",
                    cluster_id="edge-west-remote",
                    source_pvc="golden-remote-win11",
                    checksum="remotevm123",
                    size_bytes=10 * 1024 * 1024,
                    created_at=utc_now(),
                )
            )
        if not session.get(Template, "tmpl-remote-vm-1"):
            session.add(
                Template(
                    id="tmpl-remote-vm-1",
                    name="Remote VM Template",
                    tenant="global",
                    cluster_id="edge-west-remote",
                    description="remote vm test",
                    os_type="windows",
                    image_id="img-remote-vm-1",
                    cpu_cores=2,
                    ram_mb=2048,
                    enabled=True,
                    network_mode="bridge",
                    console_provider="spice",
                    created_at=utc_now(),
                )
            )
        session.commit()


def _seed_container_remote_template() -> None:
    with Session(engine) as session:
        if not session.get(ContainerImage, "img-remote-ct-1"):
            session.add(
                ContainerImage(
                    id="img-remote-ct-1",
                    name="Remote Container Image",
                    image_ref="docker.io/library/nginx:1.27",
                    tenant="global",
                    cluster_id="edge-west-remote",
                    created_at=utc_now(),
                )
            )
        if not session.get(ContainerTemplate, "tmpl-remote-ct-1"):
            session.add(
                ContainerTemplate(
                    id="tmpl-remote-ct-1",
                    template_key="tmpl-remote-ct-1",
                    version=1,
                    is_default=True,
                    name="Remote Container Template",
                    tenant="global",
                    cluster_id="edge-west-remote",
                    description="remote container test",
                    container_image_id="img-remote-ct-1",
                    cpu_millicores=500,
                    memory_mb=512,
                    container_port=80,
                    enabled=True,
                    created_at=utc_now(),
                )
            )
        session.commit()


def _login(client, username: str, password: str):
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def _ensure_remote_cluster(client) -> None:
    created = client.post(
        "/admin/settings/clusters",
        json={
            "id": "edge-west-remote",
            "name": "Edge West Remote",
            "region": "us-west",
            "enabled": True,
            "schedule_enabled": True,
            "runtime_enabled": True,
            "capacity_weight": 200,
            "kubeconfig_secret_name": "remote-kubeconfig",
            "kubeconfig_secret_namespace": "labs",
            "kubeconfig_secret_key": "kubeconfig",
        },
    )
    assert created.status_code in {201, 409}, created.text


def test_vm_launch_uses_selected_remote_cluster(client, monkeypatch) -> None:
    _login(client, "admin", "admin")
    _ensure_remote_cluster(client)
    _login(client, "alice", "password")
    _seed_vm_remote_template()
    monkeypatch.setattr(user_routes, "_kube_for_instance_cluster", lambda _session, _cluster_id: user_routes.kube)

    response = client.post("/user/templates/tmpl-remote-vm-1/start")
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["cluster_id"] == "edge-west-remote"


def test_container_launch_uses_selected_remote_cluster(client, monkeypatch) -> None:
    _login(client, "admin", "admin")
    _ensure_remote_cluster(client)
    _login(client, "alice", "password")
    _seed_container_remote_template()
    monkeypatch.setattr(
        user_container_routes,
        "_kube_for_container_cluster",
        lambda _session, _cluster_id: user_container_routes.kube,
    )

    response = client.post("/user/container-templates/tmpl-remote-ct-1/start")
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["cluster_id"] == "edge-west-remote"
