from __future__ import annotations

import zipfile

import hcl2


def _module_prefix(address: str) -> str:
    """Extract tfconfig directory prefix from a resource address.

    'module.infrastructure.azuread_service_principal.default[...]'
    → 'm-infrastructure'

    Root module → 'm-'
    """
    parts = address.split(".")
    segments: list[str] = []
    i = 0
    while i + 1 < len(parts) and parts[i] == "module":
        segments.append(parts[i + 1])
        i += 2
    return "m-" + "/m-".join(segments) if segments else "m-"


def get_attr_expr(plan_file: str, address: str, resource_type: str, resource_name: str, attr: str) -> tuple[str, str | None] | None:
    """Return (attr_expr, for_each_expr) or None if not found.

    for_each_expr is included when the attr expression uses `each`.
    """
    prefix = _module_prefix(address)
    try:
        with zipfile.ZipFile(plan_file) as zf:
            tf_files = [
                n for n in zf.namelist()
                if n.startswith(f"tfconfig/{prefix}/") and n.endswith(".tf")
            ]
            for name in tf_files:
                raw = zf.read(name).decode(errors="replace")
                try:
                    parsed = hcl2.loads(raw)
                except Exception:
                    continue
                for block in parsed.get("resource", []):
                    # python-hcl2 wraps keys in quotes: '"azuread_service_principal"'
                    resources = block.get(f'"{resource_type}"', block.get(resource_type, {}))
                    rname_key = f'"{resource_name}"'
                    if rname_key not in resources and resource_name not in resources:
                        continue
                    body = resources.get(rname_key) or resources.get(resource_name)
                    if isinstance(body, list):
                        body = body[0]
                    val = body.get(attr)
                    if val is None:
                        continue
                    expr = _expr_str(val)
                    for_each = None
                    if "each" in expr:
                        fe_val = body.get("for_each")
                        if fe_val is not None:
                            for_each = _expr_str(fe_val)
                    return expr, for_each
    except Exception:
        pass
    return None


def _expr_str(val: object) -> str:
    """Convert a python-hcl2 parsed value back to a readable expression string."""
    if isinstance(val, str):
        s = val.strip()
        if s.startswith("${") and s.endswith("}"):
            s = s[2:-1]
        return s
    if isinstance(val, list):
        return f"[{', '.join(_expr_str(v) for v in val)}]"
    if isinstance(val, dict):
        items = ", ".join(f"{k} = {_expr_str(v)}" for k, v in val.items())
        return "{" + items + "}"
    return str(val)
