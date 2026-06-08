from __future__ import annotations

import argparse
import re
import sys

from .cty import UNKNOWN
from .config import get_attr_expr, get_subscription_id_for_resource
from .ids import build_id, collect_subscription_id, resource_type, IMPORT_UNSUPPORTED
from .plan import Action, parse_plan, read_tfplan_bytes
from .resolvers import get_resolver, has_resolver, resolve_cross_plan


def _ask(prompt: str, choices: list[str]) -> str:
    choices_str = "/".join(choices)
    while True:
        try:
            ans = input(f"{prompt} [{choices_str}] > ").strip().lower()
        except EOFError:
            print(file=sys.stderr)
            return "n"
        if ans in choices:
            return ans
        print(f"Please answer {choices_str}.", file=sys.stderr)


def _ask_run(rtype: str) -> str:
    """Ask whether to run the resolve. Returns y/n/always/never/at/nt."""
    legend = f"y=yes  n=skip  always=all  never=skip all resolves  at=always {rtype}  nt=never {rtype}"
    while True:
        try:
            ans = input(f"Run?  {legend}\n> ").strip().lower()
        except EOFError:
            print(file=sys.stderr)
            return "n"
        if ans in ("y", "n", "always", "never", "at", "nt"):
            return ans
        print("Please answer y, n, always, never, at, or nt.", file=sys.stderr)


def _ask_no_resolve(rtype: str, reason: str) -> str:
    """Prompt when no resolve is possible. Returns y/n/always/at."""
    legend = (
        f"y=skip this unresolvable import  "
        f"n=stop  "
        f"always=skip all unresolvable imports  "
        f"at=skip all unresolvable {rtype} imports"
    )
    while True:
        try:
            ans = input(f"Resolve not possible ({reason}).\n{legend}\n> ").strip().lower()
        except EOFError:
            print(file=sys.stderr)
            return "y"
        if ans in ("y", "n", "always", "at"):
            return ans
        print("Please answer y, n, always, or at.", file=sys.stderr)


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
    ap.add_argument("--no-resolve", action="store_true",
                    help="Skip Azure CLI lookups; use formula-based IDs only")
    confirm_group = ap.add_mutually_exclusive_group()
    confirm_group.add_argument("--yes", action="store_true",
                    help="Accept all without prompting")
    confirm_group.add_argument("--no", action="store_true",
                    help="Reject all without prompting (dry-run)")
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

    _global_sub_id = collect_subscription_id(changes) or "<subscription-id>"

    def _sub_id_for(c) -> str:
        rtype_c = resource_type(c.address)
        rname_c = re.sub(r"\[[^\]]*\]$", "", c.address).rsplit(".", 1)[-1]
        return (get_subscription_id_for_resource(args.plan_file, c.address, rtype_c, rname_c)
                or _global_sub_id)

    if args.list:
        if args.powershell:
            print("@(")
            for i, c in enumerate(changes):
                comma = "," if i < len(changes) - 1 else ""
                print(f'    "{c.address}"{comma}')
            print(")")
        else:
            for c in changes:
                import_id, _ = build_id(c, _sub_id_for(c))
                print(f"{c.address}\t{import_id}")
        return

    # import confirm state: "always" = accept all, "never" = stop, None = ask
    if args.yes:
        import_state: str | None = "always"
    elif args.no:
        import_state = "never"
    else:
        import_state = None

    # resolve state: tracks whether to skip az CLI calls
    resolve_never_all: bool = False
    type_always: set[str] = set()
    type_never: set[str] = set()

    try:
        for c in changes:
            if import_state == "never":
                break

            rtype = resource_type(c.address)

            if rtype in IMPORT_UNSUPPORTED:
                print(f"# import not supported for {rtype}:")
                print(f"# {c.address}")
                continue

            # Cross-plan resolution is deterministic (no network) — apply it
            # automatically, like a formula, without prompting.
            cp_missing: list[str] = []
            if not args.no_resolve:
                cp_id, cp_missing = resolve_cross_plan(
                    rtype, c.after_attrs, c.address, changes, args.plan_file
                )
                if cp_id:
                    print("import {")
                    print(f"  to = {c.address}")
                    print(f'  id = "{cp_id}"')
                    print("}")
                    continue

            resolver, missing_attrs = (None, []) if args.no_resolve else get_resolver(rtype, c.after_attrs)

            if resolver:
                desc, execute = resolver
                print(f"\n{c.address}", file=sys.stderr)
                print(f"  [resolve] {desc}", file=sys.stderr)

                run_resolve = False
                if resolve_never_all:
                    print("  Resolve skipped", file=sys.stderr)
                elif rtype in type_never:
                    print("  Resolve for this resource type is skipped", file=sys.stderr)
                elif import_state == "always" or rtype in type_always:
                    run_resolve = True
                else:
                    ans = _ask_run(rtype)
                    if ans == "y":
                        run_resolve = True
                    elif ans == "n":
                        pass  # skip resolve, fall through to formula + import prompt
                    elif ans == "always":
                        import_state = "always"
                        run_resolve = True
                    elif ans == "at":
                        type_always.add(rtype)
                        run_resolve = True
                    elif ans == "nt":
                        type_never.add(rtype)
                        print("  Resolve for this resource type is skipped", file=sys.stderr)
                    elif ans == "never":
                        resolve_never_all = True
                        print("  Resolve skipped", file=sys.stderr)

                if run_resolve:
                    resolved_id, err = execute()
                    if not resolved_id:
                        msg = f"ERROR: {err}" if err else "(no result)"
                        print(f"  {msg}", file=sys.stderr)
                        formula_id, derived = build_id(c, _sub_id_for(c))
                        print(f'  Continue with placeholder id = "{formula_id}"?', file=sys.stderr)
                        ans = _ask("Continue?", ["y", "n"])
                        if ans == "n":
                            continue
                        import_id, is_derived = formula_id, derived
                    else:
                        import_id, is_derived = resolved_id, True
                else:
                    import_id, is_derived = build_id(c, _sub_id_for(c))

            else:
                import_id, is_derived = build_id(c, _sub_id_for(c))
                has_placeholder = "<" in import_id

                if rtype in type_never:
                    continue

                if has_placeholder and import_state != "always":
                    detail_attrs = missing_attrs or cp_missing
                    if detail_attrs:
                        parts = []
                        addr_stripped = re.sub(r"\[.*?\]$", "", c.address)
                        res_label = addr_stripped.rsplit(".", 1)[-1]
                        for m_attr in detail_attrs:
                            attr_name = m_attr.split(" ")[0]
                            result = get_attr_expr(args.plan_file, c.address, rtype, res_label, attr_name)
                            if result:
                                expr, for_each = result
                                suffix = f" (for_each = {for_each})" if for_each else ""
                                parts.append(f"{m_attr} = {expr}{suffix}")
                            else:
                                parts.append(m_attr)
                        reason = "; ".join(parts)
                    elif has_resolver(rtype):
                        reason = "required attributes are unknown in plan"
                    elif is_derived:
                        placeholders = re.findall(r"<([^>]+)>", import_id)
                        reason = f"computed attribute(s) in plan: {', '.join(placeholders)}"
                    else:
                        reason = "no resolver registered for this type"
                    print(f"\n{c.address}", file=sys.stderr)
                    print(f'  id = "{import_id}"', file=sys.stderr)
                    ans = _ask_no_resolve(rtype, reason)
                    if ans == "n":
                        import_state = "never"
                        break
                    if ans in ("y", "always", "at"):
                        if ans == "always":
                            import_state = "never"
                        if ans == "at":
                            type_never.add(rtype)
                        continue

            suffix = "" if is_derived else "  # TODO: unknown resource type"
            print("import {")
            print(f"  to = {c.address}")
            print(f'  id = "{import_id}"{suffix}')
            print("}")

    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
