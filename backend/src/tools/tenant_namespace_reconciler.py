from __future__ import annotations

import sys

from src.services.kubernetes import kube
from src.services.tenant_namespace_bootstrap import reconcile_all_managed_namespaces


def main() -> int:
    results = reconcile_all_managed_namespaces(kube)
    total = len(results)
    failed = [row for row in results if not row.ok]
    corrected = [row for row in results if row.ok and "drift item" in str(row.detail or "").lower()]
    succeeded = total - len(failed)
    print(
        f"tenant_namespace_reconciler: total={total} succeeded={succeeded} failed={len(failed)} "
        f"drift_corrected={len(corrected)}"
    )
    for row in corrected:
        print(f"  ~ {row.namespace}: {row.detail}")
    for row in failed:
        print(f"  - {row.namespace}: {row.detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
