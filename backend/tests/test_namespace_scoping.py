import json

from fastapi.testclient import TestClient
from sqlmodel import Session

from src.db import engine
from src.tables import ContainerImage, ContainerTemplate, Image, Instance, Template, User
from src.time_utils import utc_now


def _seed_vm_template(*, image_id: str, template_id: str, namespace: str) -> None:
    with Session(engine) as session:
        session.add(
            Image(
                id=image_id,
                name=f"Image {namespace}",
                filename=f"{image_id}.qcow2",
                tenant="global",
                namespace=namespace,
                cluster_id="local",
                source_pvc=f"golden-{image_id}",
                checksum=f"sha256-{image_id}",
                size_bytes=1024,
                created_at=utc_now(),
            )
        )
        session.add(
            Template(
                id=template_id,
                name=f"Template {namespace}",
                tenant="global",
                namespace=namespace,
                cluster_id="local",
                description="namespace scoped",
                os_type="windows",
                image_id=image_id,
                cpu_cores=2,
                ram_mb=2048,
                auto_delete_minutes=30,
                idle_timeout_minutes=30,
                enabled=True,
                created_at=utc_now(),
            )
        )
        session.commit()


def _seed_container_template(
    *,
    image_id: str,
    template_id: str,
    namespace: str,
    enabled_namespaces_json: str = "[]",
) -> None:
    with Session(engine) as session:
        session.add(
            ContainerImage(
                id=image_id,
                name=f"Container Image {namespace}",
                image_ref="docker.io/library/nginx:1.27",
                tenant="global",
                namespace=namespace,
                cluster_id="local",
                created_at=utc_now(),
            )
        )
        session.add(
            ContainerTemplate(
                id=template_id,
                template_key=f"key-{template_id}",
                version=1,
                is_default=True,
                name=f"Template {namespace}",
                tenant="global",
                namespace=namespace,
                enabled_namespaces_json=enabled_namespaces_json,
                cluster_id="local",
                container_image_id=image_id,
                cpu_millicores=500,
                memory_mb=512,
                container_port=80,
                enabled=True,
                created_at=utc_now(),
            )
        )
        session.commit()


def test_admin_images_list_honors_namespace_header(login_admin: TestClient) -> None:
    with Session(engine) as session:
        session.add(
            Image(
                id="img-ns-a",
                name="A",
                filename="a.qcow2",
                tenant="global",
                namespace="labs-a",
                cluster_id="local",
                source_pvc="golden-a",
                checksum="sha256-a",
                size_bytes=1,
                created_at=utc_now(),
            )
        )
        session.add(
            Image(
                id="img-ns-b",
                name="B",
                filename="b.qcow2",
                tenant="global",
                namespace="labs-b",
                cluster_id="local",
                source_pvc="golden-b",
                checksum="sha256-b",
                size_bytes=1,
                created_at=utc_now(),
            )
        )
        session.commit()

    scoped = login_admin.get("/admin/images", headers={"X-Bretter-Namespace": "labs-a"})
    assert scoped.status_code == 200, scoped.text
    rows = scoped.json()
    assert rows
    assert all(item["namespace"] == "labs-a" for item in rows)
    assert any(item["id"] == "img-ns-a" for item in rows)
    assert all(item["id"] != "img-ns-b" for item in rows)


def test_user_templates_list_and_start_are_namespace_scoped(login_user: TestClient) -> None:
    _seed_vm_template(image_id="img-scope-a", template_id="tmpl-scope-a", namespace="labs-a")
    _seed_vm_template(image_id="img-scope-b", template_id="tmpl-scope-b", namespace="labs-b")

    with Session(engine) as session:
        user = session.get(User, "alice")
        assert user is not None
        user.namespace_scopes_json = json.dumps(["labs-a"])
        session.add(user)
        session.commit()

    list_ok = login_user.get("/user/templates", headers={"X-Bretter-Namespace": "labs-a"})
    assert list_ok.status_code == 200, list_ok.text
    template_ids = {item["id"] for item in list_ok.json()}
    assert "tmpl-scope-a" in template_ids
    assert "tmpl-scope-b" not in template_ids

    list_forbidden = login_user.get("/user/templates", headers={"X-Bretter-Namespace": "labs-b"})
    assert list_forbidden.status_code == 403, list_forbidden.text

    start_wrong_ns = login_user.post(
        "/user/templates/tmpl-scope-b/start",
        headers={"X-Bretter-Namespace": "labs-a"},
    )
    assert start_wrong_ns.status_code == 404, start_wrong_ns.text


def test_user_container_templates_list_honors_enabled_namespace_allowlist(login_user: TestClient) -> None:
    _seed_container_template(
        image_id="img-ct-scope-a",
        template_id="ct-scope-a",
        namespace="labs-a",
        enabled_namespaces_json='["labs-b"]',
    )
    with Session(engine) as session:
        user = session.get(User, "alice")
        assert user is not None
        user.namespace_scopes_json = json.dumps(["labs-b"])
        session.add(user)
        session.commit()

    list_ok = login_user.get("/user/container-templates", headers={"X-Bretter-Namespace": "labs-b"})
    assert list_ok.status_code == 200, list_ok.text
    template_ids = {item["id"] for item in list_ok.json()}
    assert "ct-scope-a" in template_ids

    list_forbidden = login_user.get("/user/container-templates", headers={"X-Bretter-Namespace": "labs-a"})
    assert list_forbidden.status_code == 403, list_forbidden.text


def test_user_running_labs_include_template_enabled_namespace_even_with_runtime_namespace_fallback(
    login_user: TestClient,
) -> None:
    _seed_vm_template(image_id="img-vm-visible", template_id="tmpl-vm-visible", namespace="labs")
    with Session(engine) as session:
        user = session.get(User, "alice")
        assert user is not None
        user.namespace_scopes_json = json.dumps(["test-namespace"])

        template = session.get(Template, "tmpl-vm-visible")
        assert template is not None
        template.enabled_namespaces_json = json.dumps(["test-namespace"])
        session.add(template)

        session.add(
            Instance(
                id="inst-vm-visible",
                template_id="tmpl-vm-visible",
                owner="alice",
                tenant="global",
                namespace="labs-vm-priv-default",
                cluster_id="local",
                status="running",
                disk_pvc="pvc-inst-vm-visible",
                started_at=utc_now(),
                last_active_at=utc_now(),
                console_url="/user/pods/inst-vm-visible/connect/vnc.html",
            )
        )
        session.add(user)
        session.commit()

    listed = login_user.get("/user/pods", headers={"X-Bretter-Namespace": "test-namespace"})
    assert listed.status_code == 200, listed.text
    ids = {row["id"] for row in listed.json()}
    assert "inst-vm-visible" in ids

    stopped = login_user.post("/user/pods/inst-vm-visible/stop", headers={"X-Bretter-Namespace": "test-namespace"})
    assert stopped.status_code == 200, stopped.text
