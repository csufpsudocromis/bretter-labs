#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
TAGGED_DIGEST_PIN_RE = re.compile(r"^[^@\s]+:v?[0-9]+(\.[0-9]+){2}([-.+][0-9A-Za-z.-]+)?@sha256:[0-9a-f]{64}$")
ACTION_SHA_RE = re.compile(r"@[0-9a-f]{40}$")


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive
        raise RuntimeError(f"failed to parse JSON: {path}: {exc}") from exc


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_yaml_scalar(text: str, key: str) -> str:
    match = re.search(rf"^\s*{re.escape(key)}:\s*(.+?)\s*$", text, flags=re.MULTILINE)
    if not match:
        return ""
    raw = match.group(1).strip()
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1].strip()
    return raw


def _validate_workflow_action_pins(*, root: Path) -> list[str]:
    errors: list[str] = []
    workflows_dir = root / ".github" / "workflows"
    for workflow_path in sorted(workflows_dir.glob("*.yml")):
        content = _read_text(workflow_path)
        for lineno, line in enumerate(content.splitlines(), start=1):
            match = re.match(r"^\s*uses:\s*([^\s#]+)", line)
            if not match:
                continue
            action_ref = match.group(1).strip()
            if action_ref.startswith("./") or action_ref.startswith("docker://"):
                continue
            if "@" not in action_ref:
                errors.append(
                    f"{workflow_path.relative_to(root)}:{lineno} uses without @ref is not allowed ({action_ref})."
                )
                continue
            if not ACTION_SHA_RE.search(action_ref):
                errors.append(
                    f"{workflow_path.relative_to(root)}:{lineno} must pin actions to a full commit SHA ({action_ref})."
                )
    return errors


def _looks_placeholder(value: str) -> bool:
    lowered = value.lower()
    return (
        not value
        or "<" in value
        or ">" in value
        or "changeme" in lowered
        or "example" in lowered
        or lowered in {"tbd", "todo"}
    )


def _looks_local_image_ref(value: str) -> bool:
    lowered = str(value or "").strip().lower()
    if not lowered:
        return False
    return lowered.startswith("localhost/") or ":local" in lowered or "local-" in lowered


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    version_path = root / "VERSION"
    changelog_path = root / "CHANGELOG.md"
    frontend_pkg_path = root / "frontend-vite" / "package.json"
    frontend_lock_path = root / "frontend-vite" / "package-lock.json"
    values_prod_path = root / "deploy" / "helm" / "values-production.yaml"
    values_prod_site_template_path = root / "deploy" / "helm" / "values-production-site.template.yaml"
    setup_script_path = root / "scripts" / "setup.sh"
    publish_workflow_path = root / ".github" / "workflows" / "publish-and-pin-images.yml"
    deploy_production_workflow_path = root / ".github" / "workflows" / "deploy-production.yml"
    promotion_workflow_path = root / ".github" / "workflows" / "promote-staging-to-production.yml"
    post_deploy_synthetic_workflow_path = root / ".github" / "workflows" / "post-deploy-synthetic.yml"
    nightly_restore_workflow_path = root / ".github" / "workflows" / "nightly-restore-drill.yml"
    deploy_userflow_workflow_path = root / ".github" / "workflows" / "deploy-userflow-smoke.yml"
    playwright_rdp_workflow_path = root / ".github" / "workflows" / "playwright-rdp-smoke.yml"
    digest_update_script_path = root / "scripts" / "update_production_image_digests.py"
    rollback_script_path = root / "scripts" / "rollback_release.sh"
    frontend_dockerfile_path = root / "frontend-vite" / "Dockerfile"
    backend_dockerfile_path = root / "backend" / "Dockerfile"
    playwright_config_path = root / "frontend-vite" / "playwright.rdp.config.js"
    playwright_test_path = root / "frontend-vite" / "e2e" / "guacamole_rdp_smoke.spec.js"
    alertmanager_doc_path = root / "docs" / "wiki" / "Alert-Routing-and-Receiver-Defaults.md"

    errors: list[str] = []

    version = _read_text(version_path).strip()
    if not version:
        errors.append("VERSION is empty.")
    elif not SEMVER_RE.match(version):
        errors.append(f"VERSION is not valid semantic version: {version}")

    frontend_pkg = _load_json(frontend_pkg_path)
    frontend_pkg_version = str(frontend_pkg.get("version") or "").strip()
    if frontend_pkg_version != version:
        errors.append(
            "frontend-vite/package.json version mismatch: "
            f"expected {version}, found {frontend_pkg_version or '<empty>'}"
        )

    frontend_lock = _load_json(frontend_lock_path)
    lock_version = str(frontend_lock.get("version") or "").strip()
    if lock_version != version:
        errors.append(
            "frontend-vite/package-lock.json version mismatch: "
            f"expected {version}, found {lock_version or '<empty>'}"
        )
    root_pkg = frontend_lock.get("packages", {}).get("", {})
    root_pkg_version = str(root_pkg.get("version") or "").strip()
    if root_pkg_version != version:
        errors.append(
            "frontend-vite/package-lock.json packages[''].version mismatch: "
            f"expected {version}, found {root_pkg_version or '<empty>'}"
        )

    changelog = _read_text(changelog_path)
    if "## [Unreleased]" not in changelog:
        errors.append("CHANGELOG.md missing '## [Unreleased]' section.")
    version_heading = f"## [{version}]"
    if version_heading not in changelog:
        errors.append(f"CHANGELOG.md missing heading for current version: {version_heading}")

    values_production = _read_text(values_prod_path)
    if not values_prod_site_template_path.exists():
        errors.append("deploy/helm/values-production-site.template.yaml is missing.")
        values_site_template = ""
    else:
        values_site_template = _read_text(values_prod_site_template_path)

    for key in ("BACKEND_IMAGE", "BACKEND_ADMIN_IMAGE", "FRONTEND_IMAGE", "RUNNER_IMAGE"):
        image_ref = _extract_yaml_scalar(values_production, key)
        if not image_ref:
            errors.append(f"deploy/helm/values-production.yaml missing {key}.")
            continue
        if not TAGGED_DIGEST_PIN_RE.match(image_ref):
            errors.append(
                "deploy/helm/values-production.yaml must pin production images with release tag + digest "
                f"(<repo>:vX.Y.Z@sha256:...): {key}={image_ref!r}"
            )
        if _looks_local_image_ref(image_ref):
            errors.append(
                "deploy/helm/values-production.yaml must not use local/dev image references in production: "
                f"{key}={image_ref!r}"
            )
    for key in ("BACKEND_REPLICAS", "FRONTEND_REPLICAS"):
        replica_value = _extract_yaml_scalar(values_production, key).strip()
        if not replica_value:
            errors.append(f"deploy/helm/values-production.yaml missing {key}.")
            continue
        if not re.match(r"^[1-9][0-9]*$", replica_value):
            errors.append(
                f"deploy/helm/values-production.yaml {key} must be an integer >= 1 (found {replica_value!r})."
            )
    production_profile = _extract_yaml_scalar(values_production, "PRODUCTION_PROFILE")
    if production_profile != "1":
        errors.append(
            "deploy/helm/values-production.yaml must enable backend startup hardening profile: "
            f"PRODUCTION_PROFILE={production_profile!r}"
        )
    require_schema_ready = _extract_yaml_scalar(values_production, "REQUIRE_SCHEMA_READY").strip().lower()
    if require_schema_ready not in {"1", "true", "yes", "on"}:
        errors.append(
            "deploy/helm/values-production.yaml must enforce startup schema validation: "
            f"REQUIRE_SCHEMA_READY={require_schema_ready!r}"
        )
    expected_alembic_revision = _extract_yaml_scalar(values_production, "EXPECTED_ALEMBIC_REVISION").strip()
    if expected_alembic_revision and not re.match(r"^[A-Za-z0-9_]+$", expected_alembic_revision):
        errors.append(
            "deploy/helm/values-production.yaml EXPECTED_ALEMBIC_REVISION must be empty or an alphanumeric revision id."
        )

    required_production_overrides = (
        "CONTROL_NODE",
        "NODE_EXTERNAL_HOST",
        "RUNNER_NODE_SELECTOR_VALUE",
        "VM_STORAGE_CLASS",
    )
    for key in required_production_overrides:
        actual = _extract_yaml_scalar(values_production, key).strip()
        if _looks_placeholder(actual):
            errors.append(
                "deploy/helm/values-production.yaml must define concrete production values "
                f"for {key} (found {actual!r})"
            )

    orchestration_backend = _extract_yaml_scalar(values_production, "ORCHESTRATION_BACKEND").strip().lower()
    if orchestration_backend not in {"dual", "crd"}:
        errors.append("deploy/helm/values-production.yaml must set ORCHESTRATION_BACKEND to dual or crd.")

    kube_use_kvm = _extract_yaml_scalar(values_production, "KUBE_USE_KVM").strip().lower()
    vm_runner_privileged = _extract_yaml_scalar(values_production, "VM_RUNNER_PRIVILEGED").strip().lower()
    vm_net_backend = _extract_yaml_scalar(values_production, "VM_NET_BACKEND").strip().lower() or "user"
    vm_privileged_runtime_isolation_enabled = (
        _extract_yaml_scalar(values_production, "VM_PRIVILEGED_RUNTIME_ISOLATION_ENABLED").strip().lower()
    )
    vm_privileged_namespace_prefix = _extract_yaml_scalar(values_production, "VM_PRIVILEGED_NAMESPACE_PREFIX").strip()
    if vm_privileged_runtime_isolation_enabled not in {"1", "true", "yes", "on"}:
        errors.append(
            "deploy/helm/values-production.yaml must set VM_PRIVILEGED_RUNTIME_ISOLATION_ENABLED=1 when using per-team namespaces."
        )
    if _looks_placeholder(vm_privileged_namespace_prefix):
        errors.append("deploy/helm/values-production.yaml must set VM_PRIVILEGED_NAMESPACE_PREFIX.")
    elif not vm_privileged_namespace_prefix.endswith("-"):
        errors.append("deploy/helm/values-production.yaml VM_PRIVILEGED_NAMESPACE_PREFIX must end with '-'.")
    vm_needs_privileged_runtime = (
        kube_use_kvm in {"1", "true", "yes", "on"}
        or vm_runner_privileged in {"1", "true", "yes", "on"}
        or vm_net_backend == "tap-nat"
    )
    if vm_needs_privileged_runtime and vm_privileged_runtime_isolation_enabled not in {"1", "true", "yes", "on"}:
        errors.append(
            "deploy/helm/values-production.yaml must enable VM_PRIVILEGED_RUNTIME_ISOLATION_ENABLED when privileged VM runners are required."
        )

    for key in (
        "DATABASE_POOL_SIZE",
        "DATABASE_POOL_TIMEOUT_SECONDS",
        "DATABASE_POOL_RECYCLE_SECONDS",
        "DATABASE_STATEMENT_TIMEOUT_MS",
        "DATABASE_SLOW_QUERY_MS",
    ):
        raw = _extract_yaml_scalar(values_production, key).strip()
        if not raw:
            errors.append(f"deploy/helm/values-production.yaml missing {key}.")
            continue
        if not re.match(r"^[0-9]+$", raw):
            errors.append(f"deploy/helm/values-production.yaml {key} must be an integer (found {raw!r}).")
    pool_size = _extract_yaml_scalar(values_production, "DATABASE_POOL_SIZE").strip()
    if re.match(r"^[0-9]+$", pool_size) and int(pool_size) < 5:
        errors.append("deploy/helm/values-production.yaml DATABASE_POOL_SIZE must be >= 5.")
    statement_timeout = _extract_yaml_scalar(values_production, "DATABASE_STATEMENT_TIMEOUT_MS").strip()
    if re.match(r"^[0-9]+$", statement_timeout) and int(statement_timeout) < 1000:
        errors.append("deploy/helm/values-production.yaml DATABASE_STATEMENT_TIMEOUT_MS must be >= 1000.")
    slow_query = _extract_yaml_scalar(values_production, "DATABASE_SLOW_QUERY_MS").strip()
    if re.match(r"^[0-9]+$", slow_query) and int(slow_query) > 2000:
        errors.append("deploy/helm/values-production.yaml DATABASE_SLOW_QUERY_MS must be <= 2000.")

    cors_allowed_origins = _extract_yaml_scalar(values_production, "CORS_ALLOWED_ORIGINS")
    if not cors_allowed_origins:
        errors.append("deploy/helm/values-production.yaml must set CORS_ALLOWED_ORIGINS for production.")
    elif "localhost" in cors_allowed_origins.lower() or "127.0.0.1" in cors_allowed_origins:
        errors.append(
            "deploy/helm/values-production.yaml must not include localhost/127.0.0.1 in CORS_ALLOWED_ORIGINS."
        )

    runtime_secret_name = _extract_yaml_scalar(values_production, "RUNTIME_SECRETS_SECRET_NAME").strip()
    if _looks_placeholder(runtime_secret_name):
        errors.append("deploy/helm/values-production.yaml must set RUNTIME_SECRETS_SECRET_NAME.")

    runtime_secret_key = _extract_yaml_scalar(values_production, "RUNTIME_SECRETS_ENCRYPTION_KEY_KEY").strip()
    if _looks_placeholder(runtime_secret_key):
        errors.append("deploy/helm/values-production.yaml must set RUNTIME_SECRETS_ENCRYPTION_KEY_KEY.")
    elif not re.match(r"^[A-Za-z0-9._-]+$", runtime_secret_key):
        errors.append(
            "deploy/helm/values-production.yaml RUNTIME_SECRETS_ENCRYPTION_KEY_KEY contains invalid characters."
        )

    secrets_encryption_key = _extract_yaml_scalar(values_production, "SECRETS_ENCRYPTION_KEY")
    if secrets_encryption_key.strip():
        errors.append(
            "deploy/helm/values-production.yaml must keep SECRETS_ENCRYPTION_KEY empty; use runtime secret injection."
        )

    signature_verification_enabled = (
        _extract_yaml_scalar(values_production, "CONTAINER_SIGNATURE_VERIFICATION_ENABLED").strip().lower()
    )
    if signature_verification_enabled not in {"1", "true", "yes", "on"}:
        errors.append(
            "deploy/helm/values-production.yaml must enable CONTAINER_SIGNATURE_VERIFICATION_ENABLED for production."
        )
    signature_key_ref = _extract_yaml_scalar(values_production, "CONTAINER_SIGNATURE_KEY_REF").strip()
    if not signature_key_ref:
        errors.append("deploy/helm/values-production.yaml must set CONTAINER_SIGNATURE_KEY_REF for production.")
    signature_key_secret_name = _extract_yaml_scalar(values_production, "CONTAINER_SIGNATURE_KEY_SECRET_NAME").strip()
    if signature_key_ref.startswith("/etc/bretter-signing/") and not signature_key_secret_name:
        errors.append(
            "deploy/helm/values-production.yaml must set CONTAINER_SIGNATURE_KEY_SECRET_NAME when using /etc/bretter-signing key refs."
        )
    kyverno_signature_scope = _extract_yaml_scalar(values_production, "KYVERNO_SIGNATURE_SCOPE").strip().lower()
    if kyverno_signature_scope != "namespace_first_party":
        errors.append(
            "deploy/helm/values-production.yaml must set KYVERNO_SIGNATURE_SCOPE=namespace_first_party for production."
        )
    kyverno_signature_patterns = _extract_yaml_scalar(values_production, "KYVERNO_SIGNATURE_IMAGE_PATTERNS").strip()
    if not kyverno_signature_patterns:
        errors.append("deploy/helm/values-production.yaml must set KYVERNO_SIGNATURE_IMAGE_PATTERNS for production.")

    postdeploy_auth_secret_name = _extract_yaml_scalar(values_production, "POST_DEPLOY_AUTH_SECRET_NAME").strip()
    if _looks_placeholder(postdeploy_auth_secret_name):
        errors.append("deploy/helm/values-production.yaml must set POST_DEPLOY_AUTH_SECRET_NAME.")
    for key in (
        "POST_DEPLOY_AUTH_ADMIN_USERNAME_KEY",
        "POST_DEPLOY_AUTH_ADMIN_PASSWORD_KEY",
        "POST_DEPLOY_AUTH_SYNTHETIC_USERNAME_KEY",
        "POST_DEPLOY_AUTH_SYNTHETIC_PASSWORD_KEY",
    ):
        value = _extract_yaml_scalar(values_production, key).strip()
        if _looks_placeholder(value):
            errors.append(f"deploy/helm/values-production.yaml must set {key}.")

    rdp_probe_enabled = (
        _extract_yaml_scalar(values_production, "ENABLE_USERFLOW_SLO_RDP_CONNECT_LATENCY_PROBE").strip().lower()
    )
    if rdp_probe_enabled not in {"1", "true", "yes", "on"}:
        errors.append(
            "deploy/helm/values-production.yaml must enable ENABLE_USERFLOW_SLO_RDP_CONNECT_LATENCY_PROBE for production."
        )
    for key in (
        "USERFLOW_SLO_API_AUTH_SECRET_NAME",
        "USERFLOW_SLO_API_AUTH_USERNAME_KEY",
        "USERFLOW_SLO_API_AUTH_PASSWORD_KEY",
    ):
        value = _extract_yaml_scalar(values_production, key).strip()
        if _looks_placeholder(value):
            errors.append(f"deploy/helm/values-production.yaml must set {key}.")
    rdp_auth_managed = _extract_yaml_scalar(values_production, "USERFLOW_SLO_API_AUTH_MANAGED_BY_SETUP").strip().lower()
    if rdp_auth_managed not in {"0", "false", "no", "off"}:
        errors.append(
            "deploy/helm/values-production.yaml must set USERFLOW_SLO_API_AUTH_MANAGED_BY_SETUP=0 for production."
        )

    backup_replication_enabled = (
        _extract_yaml_scalar(values_production, "ENABLE_POSTGRES_BACKUP_REPLICATION").strip().lower()
    )
    if backup_replication_enabled not in {"1", "true", "yes", "on"}:
        errors.append(
            "deploy/helm/values-production.yaml must enable ENABLE_POSTGRES_BACKUP_REPLICATION for production."
        )
    for key in (
        "POSTGRES_BACKUP_REPLICATION_BUCKET",
        "POSTGRES_BACKUP_REPLICATION_SECRET_NAME",
        "POSTGRES_BACKUP_REPLICATION_ACCESS_KEY_ID_KEY",
        "POSTGRES_BACKUP_REPLICATION_SECRET_ACCESS_KEY_KEY",
    ):
        value = _extract_yaml_scalar(values_production, key).strip()
        if _looks_placeholder(value):
            errors.append(f"deploy/helm/values-production.yaml must set {key} when backup replication is required.")
    backup_sse_mode = _extract_yaml_scalar(values_production, "POSTGRES_BACKUP_REPLICATION_SSE_MODE").strip().lower()
    if backup_sse_mode not in {"aes256", "aws:kms"}:
        errors.append(
            "deploy/helm/values-production.yaml must set POSTGRES_BACKUP_REPLICATION_SSE_MODE to AES256 or aws:kms."
        )
    if backup_sse_mode == "aws:kms":
        kms_key = _extract_yaml_scalar(values_production, "POSTGRES_BACKUP_REPLICATION_SSE_KMS_KEY_ID").strip()
        if _looks_placeholder(kms_key):
            errors.append(
                "deploy/helm/values-production.yaml must set POSTGRES_BACKUP_REPLICATION_SSE_KMS_KEY_ID when SSE mode is aws:kms."
            )
    backup_object_lock_mode = (
        _extract_yaml_scalar(values_production, "POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_MODE").strip().lower()
    )
    if backup_object_lock_mode not in {"governance", "compliance"}:
        errors.append(
            "deploy/helm/values-production.yaml must set POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_MODE to GOVERNANCE or COMPLIANCE."
        )
    backup_object_lock_days = _extract_yaml_scalar(
        values_production, "POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_DAYS"
    ).strip()
    if not re.match(r"^[0-9]+$", backup_object_lock_days):
        errors.append(
            "deploy/helm/values-production.yaml POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_DAYS must be an integer."
        )
    elif int(backup_object_lock_days) < 7:
        errors.append("deploy/helm/values-production.yaml POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_DAYS must be >= 7.")

    if values_site_template:
        for key in (
            "BACKEND_REPLICAS",
            "FRONTEND_REPLICAS",
            "CONTROL_NODE",
            "NODE_EXTERNAL_HOST",
            "RUNNER_NODE_SELECTOR_VALUE",
            "VM_STORAGE_CLASS",
            "CORS_ALLOWED_ORIGINS",
            "TLS_SECRET_NAME",
            "REQUIRE_SCHEMA_READY",
            "POST_DEPLOY_AUTH_SECRET_NAME",
            "USERFLOW_SLO_API_AUTH_SECRET_NAME",
            "POSTGRES_BACKUP_REPLICATION_BUCKET",
            "POSTGRES_BACKUP_REPLICATION_SECRET_NAME",
        ):
            if not _extract_yaml_scalar(values_site_template, key).strip():
                errors.append(
                    f"deploy/helm/values-production-site.template.yaml must define {key} placeholder for site overlays."
                )

        template_secrets_key = _extract_yaml_scalar(values_site_template, "SECRETS_ENCRYPTION_KEY").strip()
        if template_secrets_key:
            errors.append("deploy/helm/values-production-site.template.yaml must keep SECRETS_ENCRYPTION_KEY empty.")

    if not digest_update_script_path.exists():
        errors.append("scripts/update_production_image_digests.py is missing.")
    if not rollback_script_path.exists():
        errors.append("scripts/rollback_release.sh is missing.")

    if not publish_workflow_path.exists():
        errors.append(".github/workflows/publish-and-pin-images.yml is missing.")
    else:
        publish_workflow = _read_text(publish_workflow_path)
        if "docker/build-push-action" not in publish_workflow:
            errors.append(
                ".github/workflows/publish-and-pin-images.yml must publish images via docker/build-push-action."
            )
        if "push:" not in publish_workflow or "main" not in publish_workflow:
            errors.append(
                ".github/workflows/publish-and-pin-images.yml must run on push to main for merge-time image promotion."
            )
        if "update_production_image_digests.py" not in publish_workflow:
            errors.append(
                ".github/workflows/publish-and-pin-images.yml must call scripts/update_production_image_digests.py."
            )
        if "--backend-admin-image" not in publish_workflow:
            errors.append(".github/workflows/publish-and-pin-images.yml must update BACKEND_ADMIN_IMAGE digest pins.")
        if (
            '--backend-image "${{ needs.publish.outputs.backend_runtime_ref }}"' not in publish_workflow
            and '--backend-image "${{ steps.refs.outputs.backend_runtime_ref }}"' not in publish_workflow
            and '--backend-image "ghcr.io/${{ steps.meta.outputs.image_namespace }}/bretter-backend-runtime@'
            not in (publish_workflow)
        ):
            errors.append(
                ".github/workflows/publish-and-pin-images.yml must pin BACKEND_IMAGE from backend_runtime digest output."
            )
        if "aquasecurity/trivy-action" not in publish_workflow:
            errors.append(".github/workflows/publish-and-pin-images.yml must scan published images with Trivy.")
        if "cosign sign --yes" not in publish_workflow or "cosign verify" not in publish_workflow:
            errors.append(".github/workflows/publish-and-pin-images.yml must sign and verify published images.")
        if (
            "scan:" not in publish_workflow
            or "sign_verify:" not in publish_workflow
            or "promote:" not in publish_workflow
        ):
            errors.append(
                ".github/workflows/publish-and-pin-images.yml must gate promotion through scan/sign/promote jobs."
            )
        if "sbom: true" not in publish_workflow:
            errors.append(".github/workflows/publish-and-pin-images.yml must enable SBOM generation for image builds.")
        if "provenance: mode=max" not in publish_workflow and "provenance: true" not in publish_workflow:
            errors.append(
                ".github/workflows/publish-and-pin-images.yml must enable provenance attestations for image builds."
            )
    if not deploy_production_workflow_path.exists():
        errors.append(".github/workflows/deploy-production.yml is missing.")
    else:
        deploy_workflow = _read_text(deploy_production_workflow_path)
        required_deploy_markers = (
            "SYNTHETIC_REQUIRE_IMAGE_UPLOAD_CHECK=1",
            "SYNTHETIC_IMAGE_UPLOAD_FILE=",
            "SYNTHETIC_IMAGE_UPLOAD_TIMEOUT_SECONDS=",
            "qemu-img create -f qcow2",
        )
        for marker in required_deploy_markers:
            if marker not in deploy_workflow:
                errors.append(
                    ".github/workflows/deploy-production.yml must enforce post-deploy upload/finalize synthetic gate "
                    f"(missing marker: {marker})."
                )

    if not promotion_workflow_path.exists():
        errors.append(".github/workflows/promote-staging-to-production.yml is missing.")
    else:
        promotion_workflow = _read_text(promotion_workflow_path)
        for marker in (
            "staging-gate:",
            "production-deploy:",
            "needs: staging-gate",
            "environment: staging",
            "environment: production",
            "scripts/deploy_preflight.sh",
            "scripts/production_go_live_proof.sh",
            "scripts/deploy_production_safe.sh",
        ):
            if marker not in promotion_workflow:
                errors.append(
                    ".github/workflows/promote-staging-to-production.yml must include staged promotion gate markers "
                    f"(missing marker: {marker})."
                )

    for workflow_path, label in (
        (post_deploy_synthetic_workflow_path, "post-deploy synthetic"),
        (nightly_restore_workflow_path, "nightly restore drill"),
    ):
        if not workflow_path.exists():
            errors.append(f"{workflow_path.relative_to(root)} is missing.")
            continue
        workflow_text = _read_text(workflow_path)
        if "pull_request:" not in workflow_text or "release/**" not in workflow_text:
            errors.append(
                f"{workflow_path.relative_to(root)} must run on pull_request to release/** for required release gates."
            )
        if "push:" not in workflow_text or "release/**" not in workflow_text:
            errors.append(
                f"{workflow_path.relative_to(root)} must run on push to release/** for required release gates."
            )
        if label == "nightly restore drill":
            if 'default: "true"' not in workflow_text:
                errors.append(".github/workflows/nightly-restore-drill.yml must default require_backup_cronjob=true.")
            if (
                "REQUIRE_BACKUP_CRONJOB: ${{ github.event.inputs.require_backup_cronjob || 'true' }}"
                not in workflow_text
            ):
                errors.append(
                    ".github/workflows/nightly-restore-drill.yml must enforce require_backup_cronjob=true by default."
                )

    if not playwright_rdp_workflow_path.exists():
        errors.append(".github/workflows/playwright-rdp-smoke.yml is missing.")
    if not deploy_userflow_workflow_path.exists():
        errors.append(".github/workflows/deploy-userflow-smoke.yml is missing.")
    else:
        deploy_userflow_workflow = _read_text(deploy_userflow_workflow_path)
        if "pull_request:" not in deploy_userflow_workflow or "main" not in deploy_userflow_workflow:
            errors.append(
                ".github/workflows/deploy-userflow-smoke.yml must run on pull_request to main for required user-flow smoke."
            )
        if "push:" not in deploy_userflow_workflow or "main" not in deploy_userflow_workflow:
            errors.append(
                ".github/workflows/deploy-userflow-smoke.yml must run on push to main for required user-flow smoke."
            )
    if not playwright_config_path.exists():
        errors.append("frontend-vite/playwright.rdp.config.js is missing.")
    if not playwright_test_path.exists():
        errors.append("frontend-vite/e2e/guacamole_rdp_smoke.spec.js is missing.")
    if not alertmanager_doc_path.exists():
        errors.append("docs/wiki/Alert-Routing-and-Receiver-Defaults.md is missing.")

    errors.extend(_validate_workflow_action_pins(root=root))

    setup_script = _read_text(setup_script_path)
    if 'DEFAULT_IMAGE_TAG="latest"' in setup_script:
        errors.append("scripts/setup.sh must not fall back to DEFAULT_IMAGE_TAG=latest.")
    if re.search(r"METRICS_SERVER_MANIFEST_URL=.*releases/latest", setup_script):
        errors.append("scripts/setup.sh must not default metrics-server manifest URL to releases/latest.")

    frontend_dockerfile = _read_text(frontend_dockerfile_path)
    if "RUN npm ci" not in frontend_dockerfile:
        errors.append("frontend-vite/Dockerfile must use `npm ci` for deterministic installs.")
    if "RUN npm install" in frontend_dockerfile:
        errors.append("frontend-vite/Dockerfile must not use `npm install` in build stage.")

    backend_dockerfile = _read_text(backend_dockerfile_path)
    if "releases/latest" in backend_dockerfile or "contrib/install.sh" in backend_dockerfile:
        errors.append("backend/Dockerfile must pin cosign/trivy downloads to explicit versions.")
    if "kubectl.sha256" not in backend_dockerfile or "sha256sum -c -" not in backend_dockerfile:
        errors.append("backend/Dockerfile must verify kubectl download integrity with checksum validation.")

    if errors:
        print("Release/version discipline checks failed:", file=sys.stderr)
        for item in errors:
            print(f"- {item}", file=sys.stderr)
        return 1

    print(f"Release/version discipline checks passed (version {version}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
