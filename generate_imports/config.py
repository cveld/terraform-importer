from __future__ import annotations

import re
import zipfile

import hcl2


def _module_prefix(address: str) -> str:
    """Extract tfconfig directory prefix from a resource address.

    Terraform names tfconfig directories `m-` + the dot-joined module call names
    (matching the `Key` field in tfconfig/modules.json). for_each/count instance
    keys are stripped — all instances of a module share one config directory.

    'module.infrastructure.module.network["vnet"].azurerm_type.name[...]'
    → 'm-infrastructure.network'

    Root module → 'm-'
    """
    return _tfdir(_module_names(address))


# ---------------------------------------------------------------------------
# Provider-based subscription ID resolution
# ---------------------------------------------------------------------------

def _module_names(address: str) -> list[str]:
    """Return module name list without for_each keys: ['infrastructure', 'network']."""
    parts = address.split(".")
    names: list[str] = []
    i = 0
    while i + 1 < len(parts) and parts[i] == "module":
        names.append(re.sub(r"\[.*\]$", "", parts[i + 1]))
        i += 2
    return names


def _tfdir(module_names: list[str]) -> str:
    """Convert a module name list to a tfconfig directory prefix.

    Terraform names these `m-` + the dot-joined module call names (matching the
    `Key` field in tfconfig/modules.json). Root module → 'm-'.
    """
    if not module_names:
        return "m-"
    return "m-" + ".".join(module_names)


def _read_module_hcl(plan_file: str, module_dir: str) -> list[dict]:
    """Parse all .tf files inside tfconfig/{module_dir}/."""
    docs: list[dict] = []
    prefix = f"tfconfig/{module_dir}/"
    try:
        with zipfile.ZipFile(plan_file) as zf:
            for name in zf.namelist():
                # exact directory match — avoid 'm-infrastructure/' matching 'm-infrastructure.rg/'
                rest = name[len(prefix):] if name.startswith(prefix) else None
                if rest is None or "/" in rest or not name.endswith(".tf"):
                    continue
                try:
                    text = zf.read(name).decode(errors="replace").replace("\r\n", "\n")
                    docs.append(hcl2.loads(text))
                except Exception:
                    pass
    except Exception:
        pass
    return docs


def _str_val(val: object) -> str:
    """Normalise a parsed HCL value to a plain string.

    Handles lists, ${...} interpolation wrappers, and surrounding quotes that
    python-hcl2 leaves on plain string literals.
    """
    if isinstance(val, list):
        val = val[0] if val else ""
    if not isinstance(val, str):
        val = _expr_str(val)
    s = val.strip()
    if s.startswith("${") and s.endswith("}"):
        s = s[2:-1].strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1]
    return s


def _get_block_body(container: dict | list | None, *keys: str) -> dict | None:
    """Extract the body dict from a labelled HCL block, trying quoted and plain key forms."""
    if container is None:
        return None
    if isinstance(container, list):
        # e.g. parsed["resource"] → list; try each entry
        for item in container:
            result = _get_block_body(item, *keys)
            if result is not None:
                return result
        return None
    for key in keys:
        for k in [f'"{key}"', key]:
            val = container.get(k)
            if val is not None:
                return val[0] if isinstance(val, list) else val
    return None


def _find_resource_provider_alias(
    docs: list[dict], resource_type: str, resource_name: str
) -> str:
    """Return the provider key for a resource ('azurerm' or 'azurerm.alias')."""
    for doc in docs:
        res_blocks = doc.get("resource", [])
        for block in res_blocks:
            rt_body = _get_block_body(block, resource_type)
            if rt_body is None:
                continue
            rn_body = _get_block_body(rt_body if isinstance(rt_body, dict) else {}, resource_name)
            if rn_body is None:
                continue
            provider_val = rn_body.get("provider")
            if provider_val is not None:
                return _str_val(provider_val)
    return "azurerm"


def _find_provider_subscription_raw(docs: list[dict], provider_key: str):
    """Find the raw subscription_id value for 'azurerm' or 'azurerm.alias'.

    Returns the unprocessed HCL value (a literal string, or '${var.x}' for a
    variable reference), or None if no matching provider block defines it.
    """
    want_alias = provider_key.split(".", 1)[1] if "." in provider_key else ""
    for doc in docs:
        for block in doc.get("provider", []):
            for pk in ['"azurerm"', "azurerm"]:
                azurerm_entries = block.get(pk, [])
                if not isinstance(azurerm_entries, list):
                    azurerm_entries = [azurerm_entries]
                for pb in azurerm_entries:
                    if not isinstance(pb, dict):
                        continue
                    got_alias = _str_val(pb.get("alias", ""))
                    if got_alias == want_alias:
                        sub = pb.get("subscription_id")
                        if sub is not None:
                            return sub
    return None


def _find_module_provider_remap(
    docs: list[dict], child_module: str, child_provider_key: str
) -> str | None:
    """Return the parent-level alias that `child_provider_key` maps to in a module call.

    e.g. `providers = { azurerm = azurerm.connectivity }` with child_provider_key='azurerm'
    returns 'azurerm.connectivity'.
    """
    for doc in docs:
        for block in doc.get("module", []):
            mod_body = _get_block_body(block, child_module)
            if mod_body is None:
                continue
            providers = mod_body.get("providers", {})
            if not isinstance(providers, dict):
                continue
            for ck in [child_provider_key, f'"{child_provider_key}"']:
                mapped = providers.get(ck)
                if mapped is not None:
                    return _str_val(mapped)
    return None


_VAR_RE = re.compile(r"^\$\{\s*var\.([A-Za-z0-9_]+)\s*\}$|^var\.([A-Za-z0-9_]+)$")


def _classify_expr(raw) -> tuple[str, str]:
    """Classify a parsed HCL value as ('literal', value), ('var', name), or ('complex', text)."""
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    if not isinstance(raw, str):
        return "complex", str(raw)
    s = raw.strip()
    m = _VAR_RE.match(s)
    if m:
        return "var", (m.group(1) or m.group(2))
    if "${" in s:
        return "complex", s
    # python-hcl2 keeps surrounding quotes on plain string literals
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1]
    return "literal", s


def _find_module_input_raw(parent_docs: list[dict], child_module: str, input_name: str):
    """Return the raw value assigned to `input_name` in a `module "<child>"` block."""
    for doc in parent_docs:
        for block in doc.get("module", []):
            mod_body = _get_block_body(block, child_module)
            if mod_body is None:
                continue
            if input_name in mod_body:
                return mod_body[input_name]
    return None


def _resolve_variable(
    plan_file: str, module_names: list[str], var_name: str, depth: int = 0
) -> str | None:
    """Resolve a `var.<name>` reference to a literal by walking up module inputs.

    At the root module (empty module_names) the value would come from tfvars/defaults
    which are not in tfconfig, so resolution stops and returns None there.
    """
    if depth > 32 or not module_names:
        return None
    parent = module_names[:-1]
    child = module_names[-1]
    parent_docs = _read_module_hcl(plan_file, _tfdir(parent))
    raw = _find_module_input_raw(parent_docs, child, var_name)
    if raw is None:
        return None
    kind, val = _classify_expr(raw)
    if kind == "literal":
        return val
    if kind == "var":
        return _resolve_variable(plan_file, parent, val, depth + 1)
    return None


def get_subscription_id_for_resource(
    plan_file: str,
    address: str,
    resource_type: str,
    resource_name: str,
) -> str | None:
    """Resolve the subscription_id of the azurerm provider used by a resource.

    Reads provider blocks and module `providers = {}` mappings from the plan's
    tfconfig, following the provider alias chain from the resource's module up to
    the root, then resolving any `var.x` reference via module input assignments.

    Algorithm:
    1. Find which provider alias the resource uses (or 'azurerm' by default).
    2. Walk up the module hierarchy:
       a. If the current level defines that provider alias's subscription_id, resolve
          it (literal → done; `var.x` → trace the variable up the module inputs).
       b. Otherwise consult the parent module call: if it remaps our provider key via
          `providers = {}`, switch to the mapped alias; the default `azurerm` is
          inherited implicitly. Continue one level up.
    """
    try:
        names = _module_names(address)

        resource_docs = _read_module_hcl(plan_file, _tfdir(names))
        provider_key = _find_resource_provider_alias(
            resource_docs, resource_type, resource_name
        )

        while True:
            docs = _read_module_hcl(plan_file, _tfdir(names))
            raw = _find_provider_subscription_raw(docs, provider_key)
            if raw is not None:
                kind, val = _classify_expr(raw)
                if kind == "literal":
                    return val
                if kind == "var":
                    return _resolve_variable(plan_file, names, val)
                return None  # complex expression — cannot resolve statically

            if not names:
                return None  # reached root without finding the provider

            parent = names[:-1]
            child = names[-1]
            parent_docs = _read_module_hcl(plan_file, _tfdir(parent))
            remapped = _find_module_provider_remap(parent_docs, child, provider_key)
            if remapped:
                provider_key = remapped
            # else: default provider inherited implicitly — keep provider_key
            names = parent

    except Exception:
        pass

    return None


def get_attr_expr(plan_file: str, address: str, resource_type: str, resource_name: str, attr: str) -> tuple[str, str | None] | None:
    """Return (attr_expr, for_each_expr) or None if not found.

    for_each_expr is included when the attr expression uses `each`.
    """
    try:
        for parsed in _read_module_hcl(plan_file, _module_prefix(address)):
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
