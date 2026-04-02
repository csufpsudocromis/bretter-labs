#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

IMMUTABLE_CODE_MOUNT_PREFIXES = ("/app/backend/src", "/app/backend/backend/src")


def _run_json(cmd: list[str]) -> Any:
    try:
        raw = subprocess.check_output(cmd, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"command failed ({exc.returncode}): {' '.join(cmd)}\n{exc.output}") from exc
    return json.loads(raw)


def _run_text(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"command failed ({exc.returncode}): {' '.join(cmd)}\n{exc.output}") from exc


def _canonical_env_item(row: dict[str, Any]) -> tuple[str, str]:
    name = str(row.get("name") or "").strip()
    if not name:
        return ("", "")
    if "value" in row:
        return (name, f"value:{row.get('value', '')}")
    value_from = row.get("valueFrom") or {}
    if not value_from:
        # Kubernetes can normalize empty env entries into an empty valueFrom object.
        return (name, "value:")
    secret = value_from.get("secretKeyRef")
    if isinstance(secret, dict):
        secret_name = str(secret.get("name") or "").strip()
        secret_key = str(secret.get("key") or "").strip()
        optional = bool(secret.get("optional", False))
        return (name, f"secret:{secret_name}:{secret_key}:{optional}")
    config_map = value_from.get("configMapKeyRef")
    if isinstance(config_map, dict):
        cm_name = str(config_map.get("name") or "").strip()
        cm_key = str(config_map.get("key") or "").strip()
        optional = bool(config_map.get("optional", False))
        return (name, f"configmap:{cm_name}:{cm_key}:{optional}")
    field_ref = value_from.get("fieldRef")
    if isinstance(field_ref, dict):
        field_path = str(field_ref.get("fieldPath") or "").strip()
        return (name, f"field:{field_path}")
    return (name, f"other:{json.dumps(value_from, sort_keys=True)}")


def _deployment_from_rendered(rendered_yaml: str, deployment_name: str) -> dict[str, Any]:
    for doc in yaml.safe_load_all(rendered_yaml):
        if not isinstance(doc, dict):
            continue
        if str(doc.get("kind") or "") != "Deployment":
            continue
        metadata = doc.get("metadata") or {}
        name = str(metadata.get("name") or "").strip()
        if name == deployment_name:
            return doc
    raise RuntimeError(f"rendered manifest does not include Deployment/{deployment_name}")


def _resource_from_rendered(rendered_yaml: str, *, kind: str, name: str) -> dict[str, Any]:
    for doc in yaml.safe_load_all(rendered_yaml):
        if not isinstance(doc, dict):
            continue
        if str(doc.get("kind") or "").strip() != kind:
            continue
        metadata = doc.get("metadata") or {}
        if str(metadata.get("name") or "").strip() == name:
            return doc
    raise RuntimeError(f"rendered manifest does not include {kind}/{name}")


def _container_map(deploy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    containers = (((deploy.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
    out: dict[str, dict[str, Any]] = {}
    for row in containers:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        out[name] = row
    return out


def _replicas(deploy: dict[str, Any]) -> int | None:
    value = ((deploy.get("spec") or {}).get("replicas")) if isinstance(deploy, dict) else None
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _compare_backend_env(expected: dict[str, Any], live: dict[str, Any], mismatches: list[str]) -> None:
    expected_backend = _container_map(expected).get("backend")
    live_backend = _container_map(live).get("backend")
    if expected_backend is None:
        mismatches.append("expected backend container missing from rendered bretter-backend deployment")
        return
    if live_backend is None:
        mismatches.append("live backend container missing from cluster bretter-backend deployment")
        return

    expected_env_rows = expected_backend.get("env") or []
    live_env_rows = live_backend.get("env") or []
    expected_env = {
        key: value
        for key, value in (_canonical_env_item(row) for row in expected_env_rows if isinstance(row, dict))
        if key
    }
    live_env = {
        key: value for key, value in (_canonical_env_item(row) for row in live_env_rows if isinstance(row, dict)) if key
    }

    for key, expected_value in sorted(expected_env.items()):
        live_value = live_env.get(key)
        if expected_value == "value:" and (live_value is None or live_value == "other:{}"):
            continue
        if live_value != expected_value:
            mismatches.append(f"backend env drift for {key}: expected={expected_value!r} live={live_value!r}")

    expected_blocked = _find_blocked_code_mounts(expected_backend)
    live_blocked = _find_blocked_code_mounts(live_backend)
    if expected_blocked:
        joined = ", ".join(expected_blocked)
        mismatches.append(f"rendered backend contains immutable-code mount override(s): {joined}")
    if live_blocked:
        joined = ", ".join(live_blocked)
        mismatches.append(f"live backend contains immutable-code mount override(s): {joined}")


def _find_blocked_code_mounts(container: dict[str, Any]) -> list[str]:
    mounts = container.get("volumeMounts") or []
    blocked: set[str] = set()
    for row in mounts:
        if not isinstance(row, dict):
            continue
        mount_path = str(row.get("mountPath") or "").strip()
        if not mount_path:
            continue
        for prefix in IMMUTABLE_CODE_MOUNT_PREFIXES:
            if mount_path == prefix or mount_path.startswith(f"{prefix}/"):
                blocked.add(mount_path)
                break
    return sorted(blocked)


def _service_signature(service: dict[str, Any]) -> tuple[str, tuple[tuple[str, str, str, str, str], ...]]:
    spec = service.get("spec") or {}
    service_type = str(spec.get("type") or "ClusterIP").strip()
    ports = spec.get("ports") or []
    normalized_ports: list[tuple[str, str, str, str, str]] = []
    for row in ports:
        if not isinstance(row, dict):
            continue
        normalized_ports.append(
            (
                str(row.get("name") or "").strip(),
                str(row.get("protocol") or "TCP").strip(),
                str(row.get("port") or "").strip(),
                str(row.get("targetPort") or "").strip(),
                str(row.get("nodePort") or "").strip(),
            )
        )
    normalized_ports.sort()
    return service_type, tuple(normalized_ports)


def _hpa_signature(hpa: dict[str, Any]) -> tuple[str, str, str]:
    spec = hpa.get("spec") or {}
    min_replicas = str(spec.get("minReplicas") if spec.get("minReplicas") is not None else "").strip()
    max_replicas = str(spec.get("maxReplicas") if spec.get("maxReplicas") is not None else "").strip()
    cpu_target = ""
    for metric in spec.get("metrics") or []:
        if not isinstance(metric, dict):
            continue
        if str(metric.get("type") or "").strip() != "Resource":
            continue
        resource = metric.get("resource") or {}
        if str(resource.get("name") or "").strip() != "cpu":
            continue
        target = resource.get("target") or {}
        cpu_target = str(
            target.get("averageUtilization") if target.get("averageUtilization") is not None else ""
        ).strip()
        break
    return min_replicas, max_replicas, cpu_target


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail when live deployment config drifts from rendered production values."
    )
    parser.add_argument("--namespace", default="labs", help="Kubernetes namespace (default: labs).")
    parser.add_argument("--release-name", default="bretter-labs", help="Helm release name for template rendering.")
    parser.add_argument(
        "--chart-dir",
        default="deploy/helm",
        help="Helm chart directory (default: deploy/helm).",
    )
    parser.add_argument(
        "-f",
        "--values-file",
        action="append",
        default=[],
        help="Helm values file (repeatable). Defaults to deploy/helm/values.yaml + deploy/helm/values-production.yaml.",
    )
    parser.add_argument(
        "--deployments",
        default="bretter-backend,bretter-frontend,bretter-labimageimport-controller",
        help="Comma-separated deployment names to compare.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    values_files = args.values_file or ["deploy/helm/values.yaml", "deploy/helm/values-production.yaml"]
    resolved_values = [str((root / item).resolve()) for item in values_files]
    for value_file in resolved_values:
        if not Path(value_file).exists():
            raise SystemExit(f"ERROR: values file not found: {value_file}")

    targets = [item.strip() for item in str(args.deployments).split(",") if item.strip()]
    if not targets:
        raise SystemExit("ERROR: at least one deployment name is required.")

    helm_cmd = [
        "helm",
        "template",
        args.release_name,
        str((root / args.chart_dir).resolve()),
        "--namespace",
        args.namespace,
    ]
    for value_file in resolved_values:
        helm_cmd.extend(["-f", value_file])
    rendered_yaml = _run_text(helm_cmd)

    mismatches: list[str] = []
    for name in targets:
        expected = _deployment_from_rendered(rendered_yaml, name)
        live = _run_json(["kubectl", "-n", args.namespace, "get", "deployment", name, "-o", "json"])

        expected_replicas = _replicas(expected)
        live_replicas = _replicas(live)
        if expected_replicas != live_replicas:
            mismatches.append(f"{name} replicas drift: expected={expected_replicas!r} live={live_replicas!r}")

        expected_containers = _container_map(expected)
        live_containers = _container_map(live)
        for container_name, expected_container in sorted(expected_containers.items()):
            live_container = live_containers.get(container_name)
            if live_container is None:
                mismatches.append(f"{name}/{container_name} missing in live deployment")
                continue
            expected_image = str(expected_container.get("image") or "").strip()
            live_image = str(live_container.get("image") or "").strip()
            if expected_image != live_image:
                mismatches.append(
                    f"{name}/{container_name} image drift: expected={expected_image!r} live={live_image!r}"
                )
            expected_pull = str(expected_container.get("imagePullPolicy") or "").strip()
            live_pull = str(live_container.get("imagePullPolicy") or "").strip()
            if expected_pull != live_pull:
                mismatches.append(
                    f"{name}/{container_name} imagePullPolicy drift: expected={expected_pull!r} live={live_pull!r}"
                )

        if name == "bretter-backend":
            _compare_backend_env(expected, live, mismatches)

    # Critical service contracts (type/ports) must match rendered values.
    for svc_name in ("bretter-backend", "bretter-frontend"):
        try:
            expected_svc = _resource_from_rendered(rendered_yaml, kind="Service", name=svc_name)
        except RuntimeError as exc:
            mismatches.append(str(exc))
            continue
        live_svc = _run_json(["kubectl", "-n", args.namespace, "get", "service", svc_name, "-o", "json"])
        expected_sig = _service_signature(expected_svc)
        live_sig = _service_signature(live_svc)
        if expected_sig != live_sig:
            mismatches.append(f"{svc_name} service contract drift: expected={expected_sig!r} live={live_sig!r}")

    # Critical autoscaling contracts must match rendered values.
    for hpa_name in ("bretter-backend", "bretter-frontend"):
        try:
            expected_hpa = _resource_from_rendered(rendered_yaml, kind="HorizontalPodAutoscaler", name=hpa_name)
        except RuntimeError as exc:
            mismatches.append(str(exc))
            continue
        live_hpa = _run_json(["kubectl", "-n", args.namespace, "get", "hpa", hpa_name, "-o", "json"])
        expected_sig = _hpa_signature(expected_hpa)
        live_sig = _hpa_signature(live_hpa)
        if expected_sig != live_sig:
            mismatches.append(f"{hpa_name} HPA drift: expected={expected_sig!r} live={live_sig!r}")

    if mismatches:
        print("FAIL: live config drift detected:", file=sys.stderr)
        for item in mismatches:
            print(f"- {item}", file=sys.stderr)
        return 1

    print(f"PASS: no live config drift detected for namespace={args.namespace} deployments={','.join(targets)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
