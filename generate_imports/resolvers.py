from __future__ import annotations

import re
import subprocess
from typing import Callable

from .cty import UNKNOWN

Resolver = tuple[str, Callable[[], tuple[str, str]]]  # (description, executor)
ResolverResult = tuple[Resolver | None, list[str]]    # (resolver_or_None, missing_attrs)


def _az(*args: str) -> tuple[str, str]:
    """Run az CLI. Returns (stdout, stderr)."""
    import sys
    try:
        r = subprocess.run(
            ["az", *args],
            capture_output=True, text=True, check=True,
            shell=(sys.platform == "win32"),
        )
        return r.stdout.strip(), ""
    except subprocess.CalledProcessError as e:
        return "", e.stderr.strip()
    except FileNotFoundError:
        return "", "az CLI not found"


def _require(attrs: dict, *keys: str) -> tuple[dict[str, str], list[str]]:
    """Return (values, missing_descriptions). Descriptions distinguish computed vs absent attrs."""
    values, missing = {}, []
    for key in keys:
        raw = attrs.get(key)
        if raw is UNKNOWN:
            missing.append(f"{key} (computed — references another resource)")
        elif raw is None or raw == "":
            missing.append(f"{key} (not in plan)")
        else:
            values[key] = str(raw)
    return values, missing


def _resolver_azuread_service_principal(a: dict, *_) -> ResolverResult:
    v, missing = _require(a, "client_id")
    if missing:
        return None, missing
    desc = f"az ad sp show --id {v['client_id']!r} --query id -o tsv"
    def execute() -> tuple[str, str]:
        return _az("ad", "sp", "show", "--id", v["client_id"], "--query", "id", "-o", "tsv")
    return (desc, execute), []


def _resolver_azuread_application(a: dict, *_) -> ResolverResult:
    v, missing = _require(a, "display_name")
    if missing:
        return None, missing
    desc = f"az ad app list --display-name {v['display_name']!r} --query [0].id -o tsv"
    def execute() -> tuple[str, str]:
        oid, err = _az("ad", "app", "list", "--display-name", v["display_name"], "--query", "[0].id", "-o", "tsv")
        return (f"/applications/{oid}" if oid else ""), err
    return (desc, execute), []


def _resolver_azuread_app_role_assignment(a: dict, *_) -> ResolverResult:
    v, missing = _require(a, "resource_object_id", "principal_object_id", "app_role_id")
    if missing:
        return None, missing
    desc = (f"az rest --method GET "
            f"--uri 'https://graph.microsoft.com/v1.0/servicePrincipals/{v['resource_object_id']}/appRoleAssignedTo' "
            f"--query \"value[?principalId=='{v['principal_object_id']}' && appRoleId=='{v['app_role_id']}'].id\" -o tsv")
    def execute() -> tuple[str, str]:
        assignment_id, err = _az(
            "rest", "--method", "GET",
            "--uri", f"https://graph.microsoft.com/v1.0/servicePrincipals/{v['resource_object_id']}/appRoleAssignedTo",
            "--query", f"value[?principalId=='{v['principal_object_id']}' && appRoleId=='{v['app_role_id']}'].id",
            "-o", "tsv",
        )
        return (f"/servicePrincipals/{v['resource_object_id']}/appRoleAssignedTo/{assignment_id}" if assignment_id else ""), err
    return (desc, execute), []


def _resolver_azuread_application_federated_identity_credential(a: dict, *_) -> ResolverResult:
    v, missing = _require(a, "application_id", "display_name")
    if missing:
        return None, missing
    app_oid = v["application_id"].removeprefix("/applications/")
    desc = (f"az rest --method GET "
            f"--uri 'https://graph.microsoft.com/v1.0/applications/{app_oid}/federatedIdentityCredentials' "
            f"--query \"value[?name=='{v['display_name']}'].id\" -o tsv")
    def execute() -> tuple[str, str]:
        cred_id, err = _az(
            "rest", "--method", "GET",
            "--uri", f"https://graph.microsoft.com/v1.0/applications/{app_oid}/federatedIdentityCredentials",
            "--query", f"value[?name=='{v['display_name']}'].id",
            "-o", "tsv",
        )
        return (f"{app_oid}/federatedIdentityCredential/{cred_id}" if cred_id else ""), err
    return (desc, execute), []


def _resolver_azurerm_role_assignment(
    a: dict, address: str | None = None, changes: list | None = None,
    plan_file: str | None = None,
) -> ResolverResult:
    # `scope` is often computed (references a sibling resource in the plan, e.g.
    # azurerm_app_configuration.conf[each.key].id). Resolve it cross-plan first
    # so the live lookup below has a concrete --scope.
    scope = a.get("scope")
    if scope is UNKNOWN or scope in (None, ""):
        scope = _resolve_scope_from_plan(address, changes, plan_file)

    missing_base: list[str] = []
    if not scope or scope is UNKNOWN:
        missing_base.append("scope (computed — references another resource)")
    v_p, missing_p = _require(a, "principal_id")
    missing_base += missing_p
    if missing_base:
        return None, missing_base

    principal = v_p["principal_id"]

    # Use --assignee-object-id (not --assignee): it bypasses the Microsoft Graph
    # lookup, which fails for managed identities / SPs the caller can't read in
    # Graph. The role is filtered client-side via JMESPath on the returned list.
    v_id, missing_id = _require(a, "role_definition_id")
    if not missing_id:
        role_def_id = v_id["role_definition_id"]
        query = f"[?roleDefinitionId=='{role_def_id}'].id | [0]"
    else:
        v_nm, missing_nm = _require(a, "role_definition_name")
        if missing_nm:
            return None, [*missing_id, *missing_nm]
        query = f"[?roleDefinitionName=='{v_nm['role_definition_name']}'].id | [0]"

    desc = (f"az role assignment list --scope {scope!r} "
            f"--assignee-object-id {principal!r} --query {query!r} -o tsv")
    def execute() -> tuple[str, str]:
        return _az(
            "role", "assignment", "list",
            "--scope", scope,
            "--assignee-object-id", principal,
            "--query", query,
            "-o", "tsv",
        )
    return (desc, execute), []


# ---------------------------------------------------------------------------
# Cross-plan resolution helpers
# ---------------------------------------------------------------------------

def _module_prefix(address: str) -> str:
    """Return the module path portion of a resource address (strips resource type+name)."""
    addr = re.sub(r"\[[^\]]*\]$", "", address)
    parts = addr.split(".")
    i = 0
    while i + 1 < len(parts) and parts[i] == "module":
        i += 2
    return ".".join(parts[:i])


def _rtype_from_addr(address: str) -> str:
    addr = re.sub(r"\[[^\]]*\]$", "", address)
    parts = addr.split(".")
    i = 0
    while i + 1 < len(parts) and parts[i] == "module":
        i += 2
    return parts[i] if i < len(parts) else address


def _resource_name(address: str) -> str:
    """Return the resource instance label (after the type, without for_each key)."""
    addr = re.sub(r"\[[^\]]*\]$", "", address)
    parts = addr.split(".")
    i = 0
    while i + 1 < len(parts) and parts[i] == "module":
        i += 2
    return parts[i + 1] if i + 1 < len(parts) else ""


def _for_each_key(address: str) -> str | None:
    m = re.search(r'\["([^"]+)"\]$', address)
    if m:
        return m.group(1)
    m = re.search(r'\[(\d+)\]$', address)
    return m.group(1) if m else None


def _ref_names_for_type(expr: str, rtype: str) -> list[str]:
    """Find resource instance names referenced in `expr` for a given resource type.

    Matches `rtype.NAME` as a top-level resource reference, excluding `data.rtype.NAME`
    and `module.x.rtype` forms (the negative lookbehind rejects a preceding `.` or word
    char). Robust against complex expressions (conditionals, try(), interpolation).

    e.g. expr='var.use_existing ? data.azurerm_virtual_network.ex.id : azurerm_virtual_network.vnet[each.key].id'
         rtype='azurerm_virtual_network' → ['vnet']
    """
    pat = re.compile(r"(?<![\w.])" + re.escape(rtype) + r"\.([A-Za-z_][A-Za-z0-9_]*)")
    seen: list[str] = []
    for m in pat.finditer(expr):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def _find_sibling(changes: list, address: str, rtype: str):
    """Find sibling resource of given type in the same module context.

    Falls back to for_each-key matching when multiple siblings exist.
    Returns (change_or_None, error_list).
    """
    prefix = _module_prefix(address)
    siblings = [c for c in changes
                if _rtype_from_addr(c.address) == rtype
                and _module_prefix(c.address) == prefix]
    if not siblings:
        return None, [f"no {rtype} found in same module"]
    if len(siblings) == 1:
        return siblings[0], []
    own_key = _for_each_key(address)
    if own_key:
        key_match = [s for s in siblings if _for_each_key(s.address) == own_key]
        if len(key_match) == 1:
            return key_match[0], []
    return None, [f"multiple {rtype} in module — cannot determine which one"]


def _find_referenced_resource(
    changes: list,
    address: str,
    plan_file: str,
    own_rtype: str,
    attr: str,
    fallback_rtype: str,
):
    """Find the plan resource that `attr` references, using the HCL config.

    Strategy:
    1. Read the HCL expression for `attr` from the plan's tfconfig.
    2. Find references to `fallback_rtype` (the expected sibling type) in the expression
       and extract the resource instance name(s).
    3. Match a change by module-prefix + type + name; disambiguate for_each by key.
    4. Fall back to type-only module-prefix matching when HCL is unavailable or no
       reference to the expected type is found in the expression.

    Returns (change_or_None, error_list).
    """
    if plan_file:
        from .config import get_attr_expr
        result = get_attr_expr(plan_file, address, own_rtype, _resource_name(address), attr)
        if result:
            expr, _ = result
            ref_names = _ref_names_for_type(expr, fallback_rtype)
            prefix = _module_prefix(address)
            for ref_name in ref_names:
                exact = [
                    c for c in changes
                    if _rtype_from_addr(c.address) == fallback_rtype
                    and _module_prefix(c.address) == prefix
                    and _resource_name(c.address) == ref_name
                ]
                if len(exact) == 1:
                    return exact[0], []
                if len(exact) > 1:
                    # for_each: disambiguate by own key
                    own_key = _for_each_key(address)
                    if own_key:
                        km = [m for m in exact if _for_each_key(m.address) == own_key]
                        if len(km) == 1:
                            return km[0], []
                    return None, [
                        f"multiple {fallback_rtype}.{ref_name} in plan for reference {expr!r}"
                    ]
            # No usable name reference — fall through to type-only matching

    return _find_sibling(changes, address, fallback_rtype)


# ---------------------------------------------------------------------------
# Cross-plan resolvers (no az CLI call; derive ID from sibling plan resource)
# ---------------------------------------------------------------------------

def _resolve_sub_id(changes: list, address: str, plan_file: str, own_rtype: str) -> str:
    """Resolve subscription_id via provider chain, falling back to attribute scanning."""
    from .ids import collect_subscription_id
    from .config import get_subscription_id_for_resource
    return (get_subscription_id_for_resource(plan_file, address, own_rtype, _resource_name(address))
            or collect_subscription_id(changes)
            or "<subscription-id>")


# Terraform reference prefixes that are not provider resource types.
_NON_RESOURCE_REFS = {"var", "local", "module", "data", "each", "self",
                      "count", "path", "terraform"}


def _resource_refs(expr: str) -> list[tuple[str, str]]:
    """Extract (resource_type, name) references from an HCL expression.

    Matches `rtype.name` where rtype looks like a provider resource type
    (lowercase with at least one underscore, e.g. `azurerm_app_configuration`).
    The negative lookbehind rejects a preceding dot, so `data.azurerm_x.y` and
    other prefixed forms are excluded.
    """
    refs: list[tuple[str, str]] = []
    pat = re.compile(r"(?<![\w.])([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\.([A-Za-z_][A-Za-z0-9_]*)")
    for m in pat.finditer(expr):
        rtype, name = m.group(1), m.group(2)
        if rtype.split("_", 1)[0] in _NON_RESOURCE_REFS:
            continue
        if (rtype, name) not in refs:
            refs.append((rtype, name))
    return refs


def _resolve_scope_from_plan(address, changes, plan_file) -> str | None:
    """Resolve an UNKNOWN role-assignment `scope` to a concrete resource ID.

    Reads the `scope` HCL expression, finds the sibling resource it references
    (any resource type), and builds that resource's import ID via `build_id`.
    Returns None if the reference can't be located or doesn't fully resolve.
    """
    if not (plan_file and changes and address):
        return None
    from .config import get_attr_expr
    from .ids import build_id

    own_rtype = _rtype_from_addr(address)
    result = get_attr_expr(plan_file, address, own_rtype, _resource_name(address), "scope")
    if not result:
        return None
    expr, _ = result

    prefix  = _module_prefix(address)
    own_key = _for_each_key(address)
    for ref_rtype, ref_name in _resource_refs(expr):
        cands = [c for c in changes
                 if _rtype_from_addr(c.address) == ref_rtype
                 and _module_prefix(c.address) == prefix
                 and _resource_name(c.address) == ref_name]
        if len(cands) > 1 and own_key:
            cands = [c for c in cands if _for_each_key(c.address) == own_key] or cands
        if len(cands) == 1:
            sub_id = _resolve_sub_id(changes, cands[0].address, plan_file, ref_rtype)
            sid, _ = build_id(cands[0], sub_id)
            if sid and "<" not in sid:
                return sid
    return None


def _resolver_azurerm_virtual_network_dns_servers(
    attrs: dict, address: str, changes: list, plan_file: str,
) -> ResolverResult:
    _, missing = _require(attrs, "virtual_network_id")
    if not missing:
        return None, []

    sibling, errors = _find_referenced_resource(
        changes, address, plan_file,
        "azurerm_virtual_network_dns_servers", "virtual_network_id",
        "azurerm_virtual_network",
    )
    if sibling is None:
        return None, [f"virtual_network_id (computed — {'; '.join(errors)})"]

    vnet_name = sibling.after_attrs.get("name")
    vnet_rg   = sibling.after_attrs.get("resource_group_name")
    if not vnet_name or vnet_name is UNKNOWN or not vnet_rg or vnet_rg is UNKNOWN:
        return None, ["virtual_network_id (computed — VNet name/resource_group not available in plan)"]

    sub_id    = _resolve_sub_id(changes, address, plan_file, "azurerm_virtual_network_dns_servers")
    vnet_id   = (f"/subscriptions/{sub_id}/resourceGroups/{vnet_rg}"
                 f"/providers/Microsoft.Network/virtualNetworks/{vnet_name}")
    import_id = f"{vnet_id}/dnsServers/default"

    desc = f"cross-plan: VNet {vnet_name!r} in rg {vnet_rg!r}"
    def execute() -> tuple[str, str]:
        return import_id, ""
    return (desc, execute), []


def _resolver_subnet_association(
    attrs: dict, address: str, changes: list, plan_file: str,
) -> ResolverResult:
    """Shared resolver for route-table and NSG subnet associations."""
    _, missing = _require(attrs, "subnet_id")
    if not missing:
        return None, []

    own_rtype = _rtype_from_addr(address)
    sibling, errors = _find_referenced_resource(
        changes, address, plan_file, own_rtype, "subnet_id", "azurerm_subnet",
    )
    if sibling is None:
        return None, [f"subnet_id (computed — {'; '.join(errors)})"]

    sn      = sibling.after_attrs
    rg      = sn.get("resource_group_name")
    vnet    = sn.get("virtual_network_name")
    sn_name = sn.get("name")
    if any(x is None or x is UNKNOWN for x in [rg, vnet, sn_name]):
        return None, ["subnet_id (computed — subnet attrs not available in plan)"]

    sub_id    = _resolve_sub_id(changes, address, plan_file, own_rtype)
    subnet_id = (f"/subscriptions/{sub_id}/resourceGroups/{rg}/providers"
                 f"/Microsoft.Network/virtualNetworks/{vnet}/subnets/{sn_name}")

    desc = f"cross-plan: subnet {sn_name!r} in VNet {vnet!r}"
    def execute() -> tuple[str, str]:
        return subnet_id, ""
    return (desc, execute), []


def _resolver_keyvault_child(id_builder: Callable) -> Callable:
    """Factory: build a cross-plan resolver for any azurerm_key_vault_* child resource."""
    def _resolver(attrs: dict, address: str, changes: list, plan_file: str) -> ResolverResult:
        _, missing = _require(attrs, "key_vault_id")
        if not missing:
            return None, []

        own_rtype = _rtype_from_addr(address)
        sibling, errors = _find_referenced_resource(
            changes, address, plan_file, own_rtype, "key_vault_id", "azurerm_key_vault",
        )
        if sibling is None:
            return None, [f"key_vault_id (computed — {'; '.join(errors)})"]

        kv      = sibling.after_attrs
        kv_name = kv.get("name")
        kv_rg   = kv.get("resource_group_name")
        if not kv_name or kv_name is UNKNOWN:
            return None, ["key_vault_id (computed — Key Vault name not available in plan)"]

        sub_id = _resolve_sub_id(changes, address, plan_file, own_rtype)
        kv_id  = (f"/subscriptions/{sub_id}/resourceGroups/{kv_rg}"
                  f"/providers/Microsoft.KeyVault/vaults/{kv_name}")

        try:
            import_id = id_builder(attrs, kv_id, kv_name)
        except Exception as exc:
            return None, [f"key_vault_id (cross-plan build failed: {exc})"]

        desc = f"cross-plan: Key Vault {kv_name!r}"
        def execute() -> tuple[str, str]:
            return import_id, ""
        return (desc, execute), []
    return _resolver


def _resolver_storage_container(
    attrs: dict, address: str, changes: list, plan_file: str,
) -> ResolverResult:
    """Resolve azurerm_storage_container when it references its account by the
    computed `storage_account_id` (azurerm v4 schema, no storage_account_name)."""
    _, missing = _require(attrs, "storage_account_id")
    if not missing:
        return None, []  # known id / older schema — let the formula handle it

    own_rtype = _rtype_from_addr(address)
    sibling, errors = _find_referenced_resource(
        changes, address, plan_file, own_rtype, "storage_account_id",
        "azurerm_storage_account",
    )
    if sibling is None:
        return None, [f"storage_account_id (computed — {'; '.join(errors)})"]

    sa        = sibling.after_attrs
    sa_name   = sa.get("name")
    sa_rg     = sa.get("resource_group_name")
    container = attrs.get("name")
    if any(x is None or x is UNKNOWN for x in [sa_name, sa_rg, container]):
        return None, ["storage_account_id (computed — storage account attrs not available in plan)"]

    sub_id    = _resolve_sub_id(changes, address, plan_file, own_rtype)
    import_id = (f"/subscriptions/{sub_id}/resourceGroups/{sa_rg}/providers"
                 f"/Microsoft.Storage/storageAccounts/{sa_name}"
                 f"/blobServices/default/containers/{container}")

    desc = f"cross-plan: storage account {sa_name!r} in rg {sa_rg!r}"
    def execute() -> tuple[str, str]:
        return import_id, ""
    return (desc, execute), []


_CROSS_PLAN_RESOLVERS: dict[str, Callable[[dict, str, list, str], ResolverResult]] = {
    "azurerm_storage_container":                          _resolver_storage_container,
    "azurerm_virtual_network_dns_servers":               _resolver_azurerm_virtual_network_dns_servers,
    "azurerm_subnet_route_table_association":             _resolver_subnet_association,
    "azurerm_subnet_network_security_group_association":  _resolver_subnet_association,
    "azurerm_key_vault_access_policy":   _resolver_keyvault_child(
        lambda a, kv_id, _: f"{kv_id}/objectId/{a.get('object_id', '<object_id>')}",
    ),
    "azurerm_key_vault_certificate":     _resolver_keyvault_child(
        lambda a, _, kv_name: f"https://{kv_name}.vault.azure.net/certificates/{a.get('name', '<name>')}",
    ),
    "azurerm_key_vault_secret":          _resolver_keyvault_child(
        lambda a, _, kv_name: f"https://{kv_name}.vault.azure.net/secrets/{a.get('name', '<name>')}",
    ),
}


_RESOLVERS: dict[str, Callable[[dict], Resolver | None]] = {
    "azurerm_role_assignment":                            _resolver_azurerm_role_assignment,
    "azuread_service_principal":                          _resolver_azuread_service_principal,
    "azuread_application":                                _resolver_azuread_application,
    "azuread_app_role_assignment":                        _resolver_azuread_app_role_assignment,
    "azuread_application_federated_identity_credential":  _resolver_azuread_application_federated_identity_credential,
}


def get_resolver(rtype: str, attrs: dict, address: str | None = None,
                 changes: list | None = None,
                 plan_file: str | None = None) -> tuple[Resolver | None, list[str]]:
    """Return (live_resolver_or_None, missing_attrs) for an `az` CLI resolver.

    Live resolvers perform a network lookup and are gated behind a confirmation
    prompt in the CLI. missing_attrs is non-empty when required attrs are unavailable.
    The cross-plan context (address/changes/plan_file) lets a live resolver first
    derive a computed attribute (e.g. a role assignment's `scope`) from a sibling.
    """
    factory = _RESOLVERS.get(rtype)
    if not factory:
        return None, []
    try:
        return factory(attrs, address, changes, plan_file)
    except Exception:
        return None, []


def resolve_cross_plan(
    rtype: str,
    attrs: dict,
    address: str,
    changes: list,
    plan_file: str,
) -> tuple[str | None, list[str]]:
    """Resolve an ID deterministically from a sibling resource in the same plan.

    Cross-plan resolvers are pure (no network), so the result is applied automatically
    without prompting. Returns:
      (id_str, [])      — resolved successfully
      (None, missing)   — a cross-plan resolver exists but a reference is unresolvable
      (None, [])        — no cross-plan resolver registered for this type
    """
    factory = _CROSS_PLAN_RESOLVERS.get(rtype)
    if not factory:
        return None, []
    try:
        resolver, missing = factory(attrs, address, changes, plan_file)
    except Exception:
        return None, []
    if resolver is None:
        return None, missing
    _, execute = resolver
    resolved_id, _err = execute()
    return (resolved_id or None), missing


def has_resolver(rtype: str) -> bool:
    """Return True if any resolver (live or cross-plan) is registered for this resource type."""
    return rtype in _RESOLVERS or rtype in _CROSS_PLAN_RESOLVERS
