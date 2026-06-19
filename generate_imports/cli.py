from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .cache import ResolveCache
from .cty import UNKNOWN
from .config import get_attr_expr, get_subscription_id_for_resource
from .ids import build_id, collect_subscription_id, resource_type, IMPORT_UNSUPPORTED
from .plan import Action, parse_plan, read_tfplan_bytes
from .resolvers import get_resolver, has_resolver, resolve_cross_plan, set_cache


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


def _import_block(address: str, import_id: str, suffix: str = "") -> str:
    return f'import {{\n  to = {address}\n  id = "{import_id}"{suffix}\n}}'


def _unresolved_reason(plan_file, c, rtype, import_id, is_derived,
                       missing_attrs, cp_missing) -> str:
    """Human-readable reason why a resource could not be fully resolved."""
    detail_attrs = missing_attrs or cp_missing
    if detail_attrs:
        parts = []
        addr_stripped = re.sub(r"\[.*?\]$", "", c.address)
        res_label = addr_stripped.rsplit(".", 1)[-1]
        for m_attr in detail_attrs:
            attr_name = m_attr.split(" ")[0]
            result = get_attr_expr(plan_file, c.address, rtype, res_label, attr_name)
            if result:
                expr, for_each = result
                suffix = f" (for_each = {for_each})" if for_each else ""
                parts.append(f"{m_attr} = {expr}{suffix}")
            else:
                parts.append(m_attr)
        return "; ".join(parts)
    if has_resolver(rtype):
        return "required attributes are unknown in plan"
    if is_derived:
        placeholders = re.findall(r"<([^>]+)>", import_id)
        return f"computed attribute(s) in plan: {', '.join(placeholders)}"
    return "no resolver registered for this type"


def _read_existing_resolved(path: Path) -> tuple[list[str], set[str]]:
    """Parse a previously generated --out file.

    Returns (preserved_blocks, resolved_addresses). Only import blocks whose id
    contains no ``<placeholder>`` are considered resolved; their addresses are
    skipped on this run and the blocks are carried over verbatim (canonicalised).
    """
    blocks: list[str] = []
    addrs: set[str] = set()
    if not path.exists():
        return blocks, addrs
    text = path.read_text(encoding="utf-8")
    for m in re.finditer(r"import\s*\{(.*?)\}", text, re.DOTALL):
        body = m.group(1)
        to_m = re.search(r"to\s*=\s*(.+)", body)
        id_m = re.search(r'id\s*=\s*"([^"]*)"(.*)', body)
        if not (to_m and id_m):
            continue
        addr = to_m.group(1).strip()
        iid = id_m.group(1)
        if "<" in iid:
            continue
        suffix = id_m.group(2).strip()
        suffix = f"  {suffix}" if suffix else ""
        addrs.add(addr)
        blocks.append(_import_block(addr, iid, suffix))
    return blocks, addrs


def _format_unresolved(items: list[dict], commented: bool) -> str:
    """Format the unresolved list. ``commented`` => fully comment every block
    (stdout mode, safe to mix into a .tf stream); otherwise emit live HCL blocks
    with the reason as a leading comment (sidecar file)."""
    out: list[str] = []
    for it in items:
        if not it["supported"]:
            out.append(f"# import not supported for {it['rtype']}:\n# {it['address']}")
            continue
        header = f"# unresolved ({it['reason']}):"
        if commented:
            out.append(
                f"{header}\n# import {{\n#   to = {it['address']}\n"
                f'#   id = "{it["id"]}"\n# }}'
            )
        else:
            out.append(f"{header}\n{_import_block(it['address'], it['id'])}")
    return "\n\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate Terraform import blocks from a binary .plan file."
    )
    ap.add_argument("plan_file", help="Path to the Terraform .plan file")
    ap.add_argument(
        "--out", metavar="FILE",
        help="Write resolved import blocks to FILE (default: stdout). Unresolved "
             "resources go to FILE.unresolved. Re-running converges: previously "
             "resolved blocks in FILE are kept and not resolved again.",
    )
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
    ap.add_argument("--no-cache", action="store_true",
                    help="Disable the persistent az-lookup cache "
                         "(<plan>.resolve-cache.json)")
    resolve_group = ap.add_mutually_exclusive_group()
    resolve_group.add_argument("--auto-resolve", action="store_true",
                    help="Run every Azure CLI resolve automatically, without prompting")
    resolve_group.add_argument("--dry-run", action="store_true",
                    help="Skip Azure CLI calls; resolve with formula + cross-plan only "
                         "(no prompts). Output files are still written.")
    args = ap.parse_args()

    cache = ResolveCache(
        None if args.no_cache else f"{args.plan_file}.resolve-cache.json",
        enabled=not args.no_cache,
    )
    set_cache(cache)

    data    = read_tfplan_bytes(args.plan_file)
    changes = parse_plan(data)

    changes = [c for c in changes if c.action == Action.CREATE]

    # `changes` stays the full CREATE set — the pool used for cross-plan sibling
    # resolution and subscription-id detection. `targets` is what we actually
    # emit; --skip-imported / --target narrow only the output, not the pool, so
    # a targeted resource can still resolve its scope/id from a sibling.
    targets = changes
    if args.skip_imported:
        targets = [c for c in targets if c.import_id is None]
    if args.target:
        targets = [c for c in targets if c.address in args.target]

    if not targets:
        print("# No matching CREATE resources found in this plan.", file=sys.stderr)
        return

    if args.debug:
        for c in targets:
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
            for i, c in enumerate(targets):
                comma = "," if i < len(targets) - 1 else ""
                print(f'    "{c.address}"{comma}')
            print(")")
        else:
            for c in targets:
                import_id, _ = build_id(c, _sub_id_for(c))
                print(f"{c.address}\t{import_id}")
        return

    file_mode = args.out is not None

    # Convergence: keep previously resolved blocks, skip those addresses.
    resolved_out: list[str] = []
    existing_addrs: set[str] = set()
    if file_mode:
        resolved_out, existing_addrs = _read_existing_resolved(Path(args.out))

    unresolved: list[dict] = []

    # resolve prompt state: "always" = auto-run every az resolve, None = ask
    import_state: str | None = "always" if args.auto_resolve else None

    # resolve state: tracks whether to skip az CLI calls.
    # --dry-run: deterministic resolution (formula + cross-plan) only, no network
    # calls, no prompts; placeholders flow to the unresolved sink.
    resolve_never_all: bool = bool(args.dry_run)
    type_always: set[str] = set()
    type_never: set[str] = set()

    try:
        for c in targets:
            if c.address in existing_addrs:
                continue  # already resolved in a previous --out run

            rtype = resource_type(c.address)

            if rtype in IMPORT_UNSUPPORTED:
                unresolved.append({"address": c.address, "rtype": rtype,
                                   "supported": False})
                continue

            # Cross-plan resolution is deterministic (no network) — always apply
            # it automatically, like a formula, without prompting.
            cp_id, cp_missing = resolve_cross_plan(
                rtype, c.after_attrs, c.address, changes, args.plan_file
            )
            if cp_id:
                resolved_out.append(_import_block(c.address, cp_id))
                continue

            resolver, missing_attrs = get_resolver(
                rtype, c.after_attrs, c.address, changes, args.plan_file
            )

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
                        pass  # skip resolve, fall through to formula
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
                        import_id, is_derived = build_id(c, _sub_id_for(c))
                    else:
                        import_id, is_derived = resolved_id, True
                else:
                    import_id, is_derived = build_id(c, _sub_id_for(c))
            else:
                import_id, is_derived = build_id(c, _sub_id_for(c))

            # Routing: a placeholder id (contains "<") is not fully resolved.
            if "<" in import_id:
                reason = _unresolved_reason(args.plan_file, c, rtype, import_id,
                                            is_derived, missing_attrs, cp_missing)
                unresolved.append({"address": c.address, "rtype": rtype,
                                   "id": import_id, "reason": reason,
                                   "supported": True})
            else:
                suffix = "" if is_derived else "  # TODO: unknown resource type"
                resolved_out.append(_import_block(c.address, import_id, suffix))

    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
    finally:
        cache.save()

    # ---- flush output -----------------------------------------------------
    if file_mode:
        out_path = Path(args.out)
        if resolved_out:
            out_path.write_text("\n\n".join(resolved_out) + "\n", encoding="utf-8")
        side = Path(f"{args.out}.unresolved")
        if unresolved:
            side.write_text(_format_unresolved(unresolved, commented=False) + "\n",
                            encoding="utf-8")
        elif side.exists():
            side.unlink()  # converged: nothing left unresolved
        print(f"# {len(resolved_out)} import block(s) -> {out_path}", file=sys.stderr)
        if unresolved:
            print(f"# {len(unresolved)} unresolved -> {side}", file=sys.stderr)
        else:
            print("# No unresolved resources.", file=sys.stderr)
    else:
        parts = list(resolved_out)
        if unresolved:
            parts.append(_format_unresolved(unresolved, commented=True))
        if parts:
            print("\n\n".join(parts))
