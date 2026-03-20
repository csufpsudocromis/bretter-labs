from __future__ import annotations

import argparse
import sys

from sqlmodel import select

from ..db import session_scope
from ..services.labinstance_crd import upsert_vm_labinstance
from ..tables import Image, Instance, Template


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill active VM DB instances into labs.bretter.io/v1alpha1 LabInstance CRDs."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include stopped/completed/failed instances (default only pending/running).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without writing CRDs.",
    )
    return parser.parse_args()


def main() -> int:
    args = _args()
    statuses = {"pending", "running"} if not args.all else None

    created = 0
    skipped = 0
    failed = 0
    with session_scope() as session:
        query = select(Instance)
        if statuses is not None:
            query = query.where(Instance.status.in_(list(statuses)))
        rows = session.exec(query).all()
        print(f"found_instances={len(rows)}")
        for row in rows:
            template = session.get(Template, row.template_id)
            if template is None:
                skipped += 1
                print(f"skip instance={row.id} reason=template-missing template_id={row.template_id}")
                continue
            image = session.get(Image, template.image_id)
            if image is None:
                skipped += 1
                print(f"skip instance={row.id} reason=image-missing image_id={template.image_id}")
                continue
            desired_state = "running" if str(row.status or "").lower() in {"pending", "running"} else "stopped"
            if args.dry_run:
                print(
                    "plan "
                    f"instance={row.id} owner={row.owner} template={template.id} image={image.id} desired={desired_state}"
                )
                created += 1
                continue
            try:
                upsert_vm_labinstance(
                    instance_id=row.id,
                    owner=row.owner,
                    template=template,
                    image=image,
                    desired_state=desired_state,
                    status_phase=str(row.status or "").capitalize() or "Pending",
                    status_message="Backfilled from DB instance state.",
                )
                created += 1
                print(f"ok instance={row.id} desired={desired_state}")
            except Exception as exc:
                failed += 1
                print(f"fail instance={row.id} error={exc}", file=sys.stderr)

    print(f"summary created={created} skipped={skipped} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
