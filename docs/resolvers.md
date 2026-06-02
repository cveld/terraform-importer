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

| resource type | az command |
|---|---|
| `azuread_service_principal` | `az ad sp show --id {client_id}` |
| `azuread_application` | `az ad app list --display-name {display_name}` |
| `azuread_app_role_assignment` | `az rest GET /servicePrincipals/{resource_object_id}/appRoleAssignedTo` filtered by `principalId` and `appRoleId` |
| `azuread_application_federated_identity_credential` | `az rest GET /applications/{app_oid}/federatedIdentityCredentials` filtered by `name` |

## Unsupported imports

Resource types that the Terraform provider does not support importing are listed in `IMPORT_UNSUPPORTED` in `ids.py`. They emit a comment block instead of an import block.

Currently: `azuread_application_password`

## Adding a new resolver

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
