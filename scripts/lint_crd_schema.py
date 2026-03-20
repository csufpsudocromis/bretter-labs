#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CRD_DIR = ROOT / "deploy" / "crds"


def _err(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)


def _load_yaml(path: Path) -> list[dict[str, Any]]:
    try:
        docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    except Exception as exc:
        _err(f"failed to parse YAML {path}: {exc}")
        return []
    out: list[dict[str, Any]] = []
    for idx, doc in enumerate(docs):
        if doc is None:
            continue
        if not isinstance(doc, dict):
            _err(f"{path}: document {idx + 1} is not a mapping")
            continue
        out.append(doc)
    return out


def _validate_crd(path: Path, doc: dict[str, Any]) -> int:
    failures = 0
    if str(doc.get("kind") or "") != "CustomResourceDefinition":
        _err(f"{path}: expected kind=CustomResourceDefinition")
        return 1
    spec = doc.get("spec")
    if not isinstance(spec, dict):
        _err(f"{path}: spec is missing or invalid")
        return 1
    names = spec.get("names")
    if not isinstance(names, dict):
        _err(f"{path}: spec.names is missing")
        failures += 1
    for field in ("group", "scope"):
        if not str(spec.get(field) or "").strip():
            _err(f"{path}: spec.{field} is required")
            failures += 1
    versions = spec.get("versions")
    if not isinstance(versions, list) or not versions:
        _err(f"{path}: spec.versions must be a non-empty list")
        return failures + 1
    storage_versions = 0
    for idx, version in enumerate(versions):
        if not isinstance(version, dict):
            _err(f"{path}: spec.versions[{idx}] is not a mapping")
            failures += 1
            continue
        if not bool(version.get("served")):
            _err(f"{path}: spec.versions[{idx}].served must be true")
            failures += 1
        if bool(version.get("storage")):
            storage_versions += 1
        subresources = version.get("subresources")
        if not isinstance(subresources, dict) or "status" not in subresources:
            _err(f"{path}: spec.versions[{idx}] must define subresources.status")
            failures += 1
        schema = version.get("schema")
        if not isinstance(schema, dict):
            _err(f"{path}: spec.versions[{idx}].schema is required")
            failures += 1
            continue
        openapi = schema.get("openAPIV3Schema")
        if not isinstance(openapi, dict):
            _err(f"{path}: spec.versions[{idx}].schema.openAPIV3Schema is required")
            failures += 1
            continue
        props = openapi.get("properties")
        if not isinstance(props, dict):
            _err(f"{path}: spec.versions[{idx}] schema must include properties map")
            failures += 1
            continue
        if "spec" not in props:
            _err(f"{path}: spec.versions[{idx}] schema must include properties.spec")
            failures += 1
        if "status" not in props:
            _err(f"{path}: spec.versions[{idx}] schema must include properties.status")
            failures += 1

    if storage_versions != 1:
        _err(f"{path}: exactly one spec.versions entry must set storage=true (found {storage_versions})")
        failures += 1
    return failures


def main() -> int:
    if not CRD_DIR.exists():
        _err(f"CRD directory not found: {CRD_DIR}")
        return 1
    files = sorted(path for path in CRD_DIR.glob("*.yaml") if path.name != "kustomization.yaml")
    if not files:
        _err("No CRD YAML files found in deploy/crds/")
        return 1
    failures = 0
    for path in files:
        docs = _load_yaml(path)
        if not docs:
            failures += 1
            continue
        for doc in docs:
            failures += _validate_crd(path, doc)
    if failures:
        _err(f"CRD schema lint failed with {failures} issue(s).")
        return 1
    print(f"CRD schema lint passed ({len(files)} file(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
