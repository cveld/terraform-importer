# Resolvers

Some resource types have IDs that are assigned by the provider (computed) and cannot be derived from plan attributes alone. For these, the tool calls the Azure CLI at runtime to look up the real ID.

## How it works

`resolvers.py` defines a `_RESOLVERS` dict mapping resource types to factory functions. Each factory:

1. Calls `_require(attrs, *keys)` to extract the needed attributes and collect any that are missing.
2. If all required attributes are present, returns a `(description, executor)` tuple:
   - `description` — a human-readable string of the `az` command (shown before running).
   - `executor` — a zero-argument callable that runs the command and returns `(id_str, error_str)`.
3. If any required attribute is missing (e.g. computed/unknown in the plan), returns `(None, missing_list)`.

The CLI calls `get_resolver(rtype, attrs)` before prompting. If a resolver exists, the user is asked to confirm before the `az` call is made.

## Built-in resolvers

### Live resolvers (az CLI)

| resource type | az command |
|---|---|
| `azurerm_role_assignment` | `az role assignment list --scope {scope} --assignee {principal_id}` filtered by `roleDefinitionId` |
| `azuread_service_principal` | `az ad sp show --id {client_id}` |
| `azuread_application` | `az ad app list --display-name {display_name}` |
| `azuread_app_role_assignment` | `az rest GET /servicePrincipals/{resource_object_id}/appRoleAssignedTo` filtered by `principalId` and `appRoleId` |
| `azuread_application_federated_identity_credential` | `az rest GET /applications/{app_oid}/federatedIdentityCredentials` filtered by `name` |

### Cross-plan resolvers (no az CLI call)

Some resources have a computed ID attribute that references a sibling resource in the same plan. Cross-plan resolvers look up the sibling's attributes (by module path and for_each key) and construct the ID formula-side, without calling az CLI.

| resource type | looks up | derives |
|---|---|---|
| `azurerm_virtual_network_dns_servers` | `azurerm_virtual_network` in same module | `{vnet_id}/dnsServers/default` |
| `azurerm_subnet_route_table_association` | `azurerm_subnet` in same module (for_each key matched) | subnet ARM ID |
| `azurerm_subnet_network_security_group_association` | `azurerm_subnet` in same module (for_each key matched) | subnet ARM ID |
| `azurerm_key_vault_access_policy` | `azurerm_key_vault` in same module | `{kv_id}/objectId/{object_id}` |
| `azurerm_key_vault_certificate` | `azurerm_key_vault` in same module | Key Vault certificate URL |
| `azurerm_key_vault_secret` | `azurerm_key_vault` in same module | Key Vault secret URL |
| `azurerm_storage_container` | `azurerm_storage_account` in same module (via computed `storage_account_id`) | container ARM ID |

Cross-plan resolvers are pure (no network), so the tool applies their result **automatically without prompting** — like a formula. They are attempted before live resolvers. If the sibling resource is not found in the plan, or if multiple siblings exist and the for_each key does not disambiguate, the resolver returns a `missing_attrs` description and the tool falls through to the normal placeholder prompt (which then shows that reason).

To find the referenced sibling, `_find_referenced_resource` first reads the HCL expression for the computed attribute (e.g. `virtual_network_id = azurerm_virtual_network.vnet.id`) and matches the exact `type.name` in the same module context. If the HCL is unavailable, it falls back to type-only matching within the module.

## Subscription ID resolution

ARM IDs embed a subscription ID. A plan can span **multiple** subscriptions — each `azurerm` provider (default or aliased) is bound to exactly one. The tool resolves the correct subscription per resource by following the provider chain through the plan's `tfconfig/`:

1. Find which provider alias the resource uses (`provider = azurerm.alias`, or the default `azurerm`).
2. Walk up the module hierarchy from the resource's module:
   - If the current module defines that provider's `subscription_id`, resolve it. A literal is used directly; a `var.x` reference is traced up through the module input assignments to its literal value.
   - Otherwise consult the parent `module "..."` call: a `providers = { azurerm = azurerm.connectivity }` mapping switches the alias being followed; the default `azurerm` is inherited implicitly.

This lives in `config.py` (`get_subscription_id_for_resource`). tfconfig directories are named `m-` + the dot-joined module call names (matching `tfconfig/modules.json`); the root module is `m-`. If resolution fails, the tool falls back to scanning resource attributes for any subscription GUID, then to the `<subscription-id>` placeholder.

## Unsupported imports

Resource types that the Terraform provider does not support importing are listed in `IMPORT_UNSUPPORTED` in `ids.py`. They emit a comment block instead of an import block.

Currently: `azuread_application_password`

## Adding a new live resolver (az CLI)

In `resolvers.py`, add a factory function and register it in `_RESOLVERS`:

```python
def _resolver_my_resource(a: dict) -> ResolverResult:
    v, missing = _require(a, "some_known_attr", "another_attr")
    if missing:
        return None, missing
    desc = f"az ... {v['some_known_attr']}"
    def execute() -> tuple[str, str]:
        result, err = _az("...", v["some_known_attr"], ...)
        return result, err
    return (desc, execute), []

_RESOLVERS["my_resource_type"] = _resolver_my_resource
```

`_require` automatically builds the missing-attribute list from the keys you pass — no duplication needed.

## Adding a new cross-plan resolver

Use `_find_sibling` to locate the referenced resource in the same module context, then construct the ID from its attributes:

```python
def _resolver_my_resource(attrs: dict, address: str, changes: list) -> ResolverResult:
    _, missing = _require(attrs, "parent_resource_id")
    if not missing:
        return None, []  # formula is sufficient

    from .ids import collect_subscription_id
    sibling, errors = _find_sibling(changes, address, "azurerm_parent_resource")
    if sibling is None:
        return None, [f"parent_resource_id (computed — {'; '.join(errors)})"]

    name = sibling.after_attrs.get("name")
    rg   = sibling.after_attrs.get("resource_group_name")
    if not name or name is UNKNOWN:
        return None, ["parent_resource_id (computed — parent name not available)"]

    sub_id    = collect_subscription_id(changes) or "<subscription-id>"
    import_id = f"/subscriptions/{sub_id}/resourceGroups/{rg}/providers/Microsoft.Foo/bars/{name}/children/{attrs.get('name', '<name>')}"

    desc = f"cross-plan: {name!r}"
    def execute() -> tuple[str, str]:
        return import_id, ""
    return (desc, execute), []

_CROSS_PLAN_RESOLVERS["azurerm_my_resource"] = _resolver_my_resource
```

`_find_sibling` matches by module prefix and falls back to for_each-key matching when multiple siblings exist.

## Adding a formula-based ID

For resources where all ID components are known at plan time, add an entry to `_ID_FORMULAS` in `ids.py`:

```python
"azurerm_my_resource":
    lambda a, s: _arm(s, _str(a, "resource_group_name"),
                      "Microsoft.MyNamespace/myResources", _str(a, "name")),
```

`_arm(sub, rg, *segments)` builds `/subscriptions/{sub}/resourceGroups/{rg}/providers/{...}`.  
`_str(attrs, key)` returns the value or `<key>` as a placeholder if the attribute is unknown.

Resources with a complete formula-based ID (no `<placeholder>`) are emitted without prompting.
