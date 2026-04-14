#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def _run_json(command: list[str]) -> dict[str, Any]:
    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "command failed").strip()
        raise RuntimeError(f"command failed ({' '.join(command)}): {message}")
    try:
        return json.loads(str(proc.stdout or "{}"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"failed to decode JSON from command {' '.join(command)}: {exc}") from exc


def _exec_backend_python(namespace: str, script_body: str) -> dict[str, Any]:
    encoded = base64.b64encode(script_body.encode("utf-8")).decode("ascii")
    command = [
        "kubectl",
        "-n",
        namespace,
        "exec",
        "deploy/bretter-backend",
        "--",
        "env",
        "PYTHONPATH=/app/backend",
        "python",
        "-c",
        f"import base64;exec(base64.b64decode('{encoded}').decode('utf-8'))",
    ]
    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "backend query failed").strip()
        raise RuntimeError(message)
    lines = [line.strip() for line in str(proc.stdout or "").splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("backend query returned empty output")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"backend query returned non-JSON output: {lines[-1][:200]}") from exc


def _fetch_backend_catalog(namespace: str) -> dict[str, Any]:
    script = """
import json
from sqlmodel import Session, select

from src.db import engine
from src.tables import ContainerTemplate, Image, ManagedNamespace, Template

with Session(engine) as session:
    managed = []
    for row in session.exec(select(ManagedNamespace)).all():
        managed.append(
            {
                "namespace": str(getattr(row, "namespace", "") or "").strip().lower(),
                "enabled": bool(getattr(row, "enabled", True)),
            }
        )
    images = []
    for row in session.exec(select(Image)).all():
        images.append(
            {
                "id": str(getattr(row, "id", "") or "").strip(),
                "name": str(getattr(row, "name", "") or "").strip(),
                "namespace": str(getattr(row, "namespace", "") or "").strip().lower(),
                "shared_catalog": bool(getattr(row, "shared_catalog", False)),
                "source_pvc": str(getattr(row, "source_pvc", "") or "").strip(),
            }
        )
    vm_templates = []
    for row in session.exec(select(Template)).all():
        vm_templates.append(
            {
                "id": str(getattr(row, "id", "") or "").strip(),
                "name": str(getattr(row, "name", "") or "").strip(),
                "namespace": str(getattr(row, "namespace", "") or "").strip().lower(),
                "enabled": bool(getattr(row, "enabled", False)),
                "enabled_namespaces_json": str(getattr(row, "enabled_namespaces_json", "[]") or "[]"),
                "image_id": str(getattr(row, "image_id", "") or "").strip(),
            }
        )
    container_templates = []
    for row in session.exec(select(ContainerTemplate)).all():
        container_templates.append(
            {
                "id": str(getattr(row, "id", "") or "").strip(),
                "name": str(getattr(row, "name", "") or "").strip(),
                "namespace": str(getattr(row, "namespace", "") or "").strip().lower(),
                "enabled": bool(getattr(row, "enabled", False)),
                "enabled_namespaces_json": str(getattr(row, "enabled_namespaces_json", "[]") or "[]"),
            }
        )

print(
    json.dumps(
        {
            "managed_namespaces": managed,
            "images": images,
            "vm_templates": vm_templates,
            "container_templates": container_templates,
        }
    )
)
"""
    return _exec_backend_python(namespace=namespace, script_body=script)


def _parse_enabled_namespaces(raw: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    out: list[str] = []
    for item in payload:
        namespace = str(item or "").strip().lower()
        if not namespace or namespace in out:
            continue
        out.append(namespace)
    return out


def _build_drift_report(namespace: str) -> dict[str, Any]:
    backend = _fetch_backend_catalog(namespace=namespace)
    namespace_payload = _run_json(["kubectl", "get", "namespace", "-o", "json"])
    pvc_payload = _run_json(["kubectl", "-n", namespace, "get", "pvc", "-o", "json"])

    managed_rows = backend.get("managed_namespaces") or []
    image_rows = backend.get("images") or []
    vm_template_rows = backend.get("vm_templates") or []
    container_template_rows = backend.get("container_templates") or []

    cluster_namespaces = {
        str(((item or {}).get("metadata") or {}).get("name") or "").strip().lower()
        for item in (namespace_payload.get("items") or [])
    }
    cluster_pvcs = {
        str(((item or {}).get("metadata") or {}).get("name") or "").strip() for item in (pvc_payload.get("items") or [])
    }

    enabled_managed = sorted(
        {
            str((row or {}).get("namespace") or "").strip().lower()
            for row in managed_rows
            if bool((row or {}).get("enabled", True))
        }
    )
    disabled_managed = sorted(
        {
            str((row or {}).get("namespace") or "").strip().lower()
            for row in managed_rows
            if not bool((row or {}).get("enabled", True))
        }
    )

    errors: list[str] = []
    warnings: list[str] = []

    for managed_namespace in enabled_managed:
        if managed_namespace not in cluster_namespaces:
            errors.append(f"enabled managed namespace missing from cluster: {managed_namespace}")

    for managed_namespace in disabled_managed:
        if managed_namespace in cluster_namespaces:
            warnings.append(f"disabled managed namespace still exists in cluster: {managed_namespace}")

    image_ids = {
        str((row or {}).get("id") or "").strip() for row in image_rows if str((row or {}).get("id") or "").strip()
    }
    image_source_pvcs = {
        str((row or {}).get("source_pvc") or "").strip()
        for row in image_rows
        if str((row or {}).get("source_pvc") or "").strip()
    }
    for pvc_name in sorted(image_source_pvcs):
        if pvc_name not in cluster_pvcs:
            errors.append(f"image source PVC missing in control namespace {namespace}: {pvc_name}")

    vm_templates_enabled = 0
    container_templates_enabled = 0
    namespace_template_targets = Counter()

    for row in vm_template_rows:
        row_id = str((row or {}).get("id") or "").strip()
        row_name = str((row or {}).get("name") or "").strip() or row_id
        if not bool((row or {}).get("enabled", False)):
            continue
        vm_templates_enabled += 1
        image_id = str((row or {}).get("image_id") or "").strip()
        if image_id and image_id not in image_ids:
            errors.append(f"enabled VM template references missing image ({row_name} -> {image_id})")
        enabled_namespaces = _parse_enabled_namespaces(str((row or {}).get("enabled_namespaces_json") or "[]"))
        for target_namespace in enabled_namespaces:
            namespace_template_targets[target_namespace] += 1
            if target_namespace not in cluster_namespaces:
                errors.append(
                    f"enabled VM template targets missing cluster namespace: {row_name} -> {target_namespace}"
                )
            elif target_namespace not in enabled_managed:
                warnings.append(
                    f"enabled VM template targets namespace not currently enabled in managed catalog: "
                    f"{row_name} -> {target_namespace}"
                )

    for row in container_template_rows:
        row_id = str((row or {}).get("id") or "").strip()
        row_name = str((row or {}).get("name") or "").strip() or row_id
        if not bool((row or {}).get("enabled", False)):
            continue
        container_templates_enabled += 1
        enabled_namespaces = _parse_enabled_namespaces(str((row or {}).get("enabled_namespaces_json") or "[]"))
        for target_namespace in enabled_namespaces:
            namespace_template_targets[target_namespace] += 1
            if target_namespace not in cluster_namespaces:
                errors.append(
                    f"enabled container template targets missing cluster namespace: {row_name} -> {target_namespace}"
                )
            elif target_namespace not in enabled_managed:
                warnings.append(
                    "enabled container template targets namespace not currently enabled in managed catalog: "
                    f"{row_name} -> {target_namespace}"
                )

    for managed_namespace in enabled_managed:
        if namespace_template_targets.get(managed_namespace, 0) == 0:
            warnings.append(f"enabled managed namespace has no enabled template targets: {managed_namespace}")

    return {
        "namespace": namespace,
        "summary": {
            "managed_enabled_count": len(enabled_managed),
            "managed_disabled_count": len(disabled_managed),
            "image_count": len(image_rows),
            "vm_templates_enabled_count": vm_templates_enabled,
            "container_templates_enabled_count": container_templates_enabled,
            "errors": len(errors),
            "warnings": len(warnings),
        },
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check drift between backend catalog state (namespaces/templates/images) and live Kubernetes objects."
    )
    parser.add_argument("--namespace", default="labs", help="Control-plane namespace running bretter-backend.")
    parser.add_argument("--report", default="", help="Optional JSON report output path.")
    parser.add_argument(
        "--fail-on-warn",
        action="store_true",
        help="Treat warnings as failures (non-zero exit).",
    )
    args = parser.parse_args()

    report = _build_drift_report(namespace=str(args.namespace).strip() or "labs")
    report_path = str(args.report or "").strip()
    if report_path:
        output_path = Path(report_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote platform drift report: {output_path}")

    summary = report.get("summary") or {}
    print(
        "platform drift summary: "
        f"errors={summary.get('errors', 0)} warnings={summary.get('warnings', 0)} "
        f"managed_enabled={summary.get('managed_enabled_count', 0)} "
        f"vm_templates_enabled={summary.get('vm_templates_enabled_count', 0)} "
        f"container_templates_enabled={summary.get('container_templates_enabled_count', 0)}"
    )
    for item in report.get("errors") or []:
        print(f"ERROR: {item}", file=sys.stderr)
    for item in report.get("warnings") or []:
        print(f"WARN: {item}", file=sys.stderr)

    if (report.get("errors") or []) or (args.fail_on_warn and (report.get("warnings") or [])):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
