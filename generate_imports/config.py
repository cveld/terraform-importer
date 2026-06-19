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


# ---------------------------------------------------------------------------
# Cross-module reference chain tracing
# ---------------------------------------------------------------------------
#
# Resolves a computed attribute that references another resource through module
# boundaries — module inputs (`var.x`), locals (`local.x`), and module outputs
# (`module.m.out`) — down to the target resource. This is genuine multi-hop
# resolution (unlike get_attr_expr, which only shows one expression verbatim).

def _module_context(address: str) -> list[tuple[str, str | None]]:
    """Parse a resource address into its module path as (name, instance_key) pairs.

    'module.infra.module.kv["core"].azurerm_x.n' → [('infra', None), ('kv', 'core')]
    """
    parts = address.split(".")
    ctx: list[tuple[str, str | None]] = []
    i = 0
    while i + 1 < len(parts) and parts[i] == "module":
        seg = parts[i + 1]
        m = re.match(r'([^\[]+)(?:\[\s*"?([^"\]]+)"?\s*\])?$', seg)
        if m:
            ctx.append((m.group(1), m.group(2)))
        i += 2
    return ctx


def _split_ref(expr: str) -> list[str]:
    """Tokenise an HCL reference, normalising index access to dot tokens.

    '${module.kv["core"].vault.id}' → ['module', 'kv', 'core', 'vault', 'id']
    """
    s = expr.strip()
    if s.startswith("${") and s.endswith("}"):
        s = s[2:-1].strip()
    s = re.sub(r'\[\s*"([^"]*)"\s*\]', r".\1", s)
    s = re.sub(r"\[\s*'([^']*)'\s*\]", r".\1", s)
    s = re.sub(r"\[\s*(\d+)\s*\]", r".\1", s)
    return [t for t in s.split(".") if t]


_REF_PREFIXES = ("var", "local", "module")


def _ref_tokens(expr: str) -> list[str]:
    """Tokenise a reference, looking through function calls / complex expressions.

    A clean reference (`var.x`, `module.m.out`, `azurerm_x.y`) tokenises directly.
    Otherwise (e.g. `merge(azurerm_resource_group.groups, data.azurerm_x.ex)`),
    extract the first concrete resource reference — a `type.name` with an
    underscore in the type, not prefixed by `data.`/another segment.
    """
    toks = _split_ref(expr)
    if toks and (toks[0] in _REF_PREFIXES
                 or (re.fullmatch(r"[a-z][a-z0-9_]*", toks[0]) and "_" in toks[0])):
        return toks
    m = re.search(r"(?<![\w.])([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\.([A-Za-z_]\w*)", expr)
    if m:
        return [m.group(1), m.group(2)]
    return toks


def _index_into(val: object, path: list[str]) -> tuple[str, list[str]]:
    """Descend a parsed HCL object along `path` field names.

    Stops as soon as the value is no longer an object (e.g. a reference string),
    returning the remaining path so the caller can apply it to whatever the
    reference resolves to.
    """
    if isinstance(val, list) and len(val) == 1:
        val = val[0]
    while path and isinstance(val, dict):
        field = path[0]
        if field in val:
            val = val[field]
        elif f'"{field}"' in val:
            val = val[f'"{field}"']
        else:
            break
        if isinstance(val, list) and len(val) == 1:
            val = val[0]
        path = path[1:]
    return _expr_str(val), path


def _find_local_raw(docs: list[dict], name: str):
    for doc in docs:
        for block in doc.get("locals", []):
            if name in block:
                return block[name]
            if f'"{name}"' in block:
                return block[f'"{name}"']
    return None


def _find_output_raw(docs: list[dict], name: str):
    for doc in docs:
        for block in doc.get("output", []):
            body = _get_block_body(block, name)
            if body is not None and "value" in body:
                return body["value"]
    return None


def _output_names(docs: list[dict]) -> set[str]:
    names: set[str] = set()
    for doc in docs:
        for block in doc.get("output", []):
            for k in block:
                names.add(k.strip('"'))
    return names


def trace_reference(read_module, context, tokens, depth: int = 0):
    """Resolve a reference token list to its target resource.

    `read_module(module_names)` returns the parsed HCL docs for a module (the
    dependency injection point — production passes a zip-backed reader, tests a
    dict-backed fake). `context` is the current module path as (name, key) pairs.

    Returns (target_context, resource_type, resource_name) or None.
    """
    if depth > 32 or not tokens:
        return None
    head = tokens[0]

    if head == "var":
        if len(tokens) < 2 or not context:
            return None
        var_name, rest = tokens[1], tokens[2:]
        child_name = context[-1][0]
        parent = context[:-1]
        raw = _find_module_input_raw(read_module([n for n, _ in parent]), child_name, var_name)
        if raw is None:
            return None
        expr2, rest2 = _index_into(raw, rest)
        return trace_reference(read_module, parent, _ref_tokens(expr2) + rest2, depth + 1)

    if head == "local":
        if len(tokens) < 2:
            return None
        name, rest = tokens[1], tokens[2:]
        raw = _find_local_raw(read_module([n for n, _ in context]), name)
        if raw is None:
            return None
        expr2, rest2 = _index_into(raw, rest)
        return trace_reference(read_module, context, _ref_tokens(expr2) + rest2, depth + 1)

    if head == "module":
        if len(tokens) < 3:
            return None
        mod_name = tokens[1]
        child_docs = read_module([n for n, _ in context] + [mod_name])
        outputs = _output_names(child_docs)
        idx, key = 2, None
        if tokens[idx] not in outputs:          # for_each/count instance key
            key, idx = tokens[idx], idx + 1
        if idx >= len(tokens):
            return None
        out_name, rest = tokens[idx], tokens[idx + 1:]
        raw = _find_output_raw(child_docs, out_name)
        if raw is None:
            return None
        expr2, rest2 = _index_into(raw, rest)
        return trace_reference(read_module, context + [(mod_name, key)],
                               _ref_tokens(expr2) + rest2, depth + 1)

    # Resource reference: <type>.<name>[...]
    if len(tokens) >= 2:
        return (context, tokens[0], tokens[1])
    return None


def trace_attr_to_resource(plan_file: str, address: str, resource_type: str,
                           resource_name: str, attr: str):
    """Trace a computed attribute's reference chain to its target resource.

    Returns (target_context, resource_type, resource_name) or None.
    """
    result = get_attr_expr(plan_file, address, resource_type, resource_name, attr)
    if not result:
        return None
    expr, _ = result
    reader = lambda names: _read_module_hcl(plan_file, _tfdir(names))  # noqa: E731
    return trace_reference(reader, _module_context(address), _ref_tokens(expr))


def resolve_value(read_module, context, tokens, depth: int = 0):
    """Resolve a var/local reference to its parsed HCL *value* (e.g. a map).

    Unlike trace_reference (which finds a target resource), this returns the
    data the reference points at, paired with the module context in which that
    data's expressions are written — so callers can trace those further.

    Returns (value, defining_context) or None.
    """
    if depth > 32 or not tokens:
        return None
    head = tokens[0]
    if head == "var":
        if len(tokens) < 2 or not context:
            return None
        name, rest = tokens[1], tokens[2:]
        parent = context[:-1]
        raw = _find_module_input_raw(read_module([n for n, _ in parent]), context[-1][0], name)
        if raw is None:
            return None
        return _value_from_raw(read_module, parent, raw, rest, depth)
    if head == "local":
        if len(tokens) < 2:
            return None
        name, rest = tokens[1], tokens[2:]
        raw = _find_local_raw(read_module([n for n, _ in context]), name)
        if raw is None:
            return None
        return _value_from_raw(read_module, context, raw, rest, depth)
    return None


def _value_from_raw(read_module, ctx, raw, rest, depth):
    if isinstance(raw, list) and len(raw) == 1:
        raw = raw[0]
    if isinstance(raw, str):
        toks = _split_ref(raw)
        if toks and toks[0] in ("var", "local"):
            return resolve_value(read_module, ctx, toks + rest, depth + 1)
        return (raw, ctx)  # a literal or other expression string
    # structured value (dict/list): descend the remaining field path
    val = raw
    for field in rest:
        if isinstance(val, list) and len(val) == 1:
            val = val[0]
        if isinstance(val, dict):
            if field in val:
                val = val[field]
            elif f'"{field}"' in val:
                val = val[f'"{field}"']
            else:
                return None
        else:
            return None
    return (val, ctx)
