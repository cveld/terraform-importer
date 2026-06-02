from __future__ import annotations

import argparse
import sys

from .cty import UNKNOWN
from .ids import build_id, collect_subscription_id
from .plan import Action, parse_plan, read_tfplan_bytes


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate Terraform import blocks from a binary .plan file."
    )
    ap.add_argument("plan_file", help="Path to the Terraform .plan file")
    ap.add_argument(
        "--skip-imported", action="store_true",
        help="Skip resources that already have an import block in the config",
    )
    ap.add_argument("--target", nargs="*", default=[], metavar="ADDR",
                    help="Only include these resource addresses")
    ap.add_argument("--list", action="store_true",
                    help="Print address/id pairs instead of import blocks")
    ap.add_argument("--powershell", action="store_true",
                    help="With --list: output a PowerShell array literal of addresses")
    ap.add_argument("--debug", action="store_true",
                    help="Dump decoded attributes for each CREATE resource")
    args = ap.parse_args()

    data    = read_tfplan_bytes(args.plan_file)
    changes = parse_plan(data)

    changes = [c for c in changes if c.action == Action.CREATE]

    if args.skip_imported:
        changes = [c for c in changes if c.import_id is None]

    if args.target:
        changes = [c for c in changes if c.address in args.target]

    if not changes:
        print("# No matching CREATE resources found in this plan.", file=sys.stderr)
        return

    if args.debug:
        for c in changes:
            print(f"\n=== {c.address}")
            print(f"    action      : {c.action.name}")
            print(f"    import_id   : {c.import_id!r}")
            attrs = c.after_attrs
            if not attrs:
                print("    after_attrs : (empty — msgpack decode produced nothing)")
            else:
                for k, v in sorted(attrs.items()):
                    val_str = "<UNKNOWN>" if v is UNKNOWN else repr(v)
                    print(f"    {k:30s} = {val_str}")
        return

    sub_id = collect_subscription_id(changes) or "<subscription-id>"

    if args.list:
        if args.powershell:
            print("@(")
            for i, c in enumerate(changes):
                comma = "," if i < len(changes) - 1 else ""
                print(f'    "{c.address}"{comma}')
            print(")")
        else:
            for c in changes:
                import_id, _ = build_id(c, sub_id)
                print(f"{c.address}\t{import_id}")
        return

    for c in changes:
        import_id, derived = build_id(c, sub_id)
        suffix = "" if derived else "  # TODO: unknown resource type"
        print("import {")
        print(f"  to = {c.address}")
        print(f'  id = "{import_id}"{suffix}')
        print("}")
