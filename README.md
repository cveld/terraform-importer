# generate-imports-from-plan

Generates Terraform `import {}` blocks directly from a binary `.plan` file — no `terraform` CLI, no provider plugins, no `terraform init` required.

## Why?

`terraform show -json` does not expose all planned attribute values — computed fields like resource names and resource group names are missing from the JSON output. This tool reads the raw msgpack-encoded attributes directly from the binary plan, giving access to all values needed to construct Azure resource IDs.

## Installation

```sh
uvx generate-imports-from-plan terraform.plan
```

Or install permanently:

```sh
uv tool install generate-imports-from-plan
generate-imports terraform.plan
```

## Workflow

```powershell
# 1. Generate plan
terraform plan -out terraform.plan

# 2. Generate import blocks and write to file
generate-imports terraform.plan > imports.tf

# 3. Fill in any remaining # TODO ids, then re-plan and apply
terraform plan -out terraform.plan
terraform apply terraform.plan
```

## Output

```hcl
import {
  to = module.core.azurerm_resource_group.this
  id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-myapp-dev-we-01"
}

import {
  to = module.core.azurerm_storage_account.this
  id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-myapp-dev-we-01/providers/Microsoft.Storage/storageAccounts/stmyapp01"
}
```

Resources whose type is not in the built-in formula table get an empty `id` with a `# TODO` comment.

## Flags

| Flag | Description |
|---|---|
| `--skip-imported` | Skip resources that already have an `import {}` block in the config |
| `--list` | Print `address\tid` pairs instead of HCL blocks |
| `--list --powershell` | Output a PowerShell array literal of resource addresses |
| `--target ADDR [ADDR ...]` | Only process the specified resource addresses |
| `--debug` | Dump all decoded attributes per resource — useful when an ID shows a placeholder |

## Adding a resource type

If a resource type is missing, extend `_ID_FORMULAS` in `generate_imports.py`:

```python
"azurerm_my_resource":
    lambda a, s: _arm(s, _str(a, "resource_group_name"),
                      "Microsoft.MyNamespace/myResources", _str(a, "name")),
```

`_arm(sub, rg, *segments)` builds `/subscriptions/{sub}/resourceGroups/{rg}/providers/{...}`.  
`_str(attrs, key)` returns the attribute value or `<key>` as a placeholder when unknown.

Use `--debug` to see all available attribute names for an unrecognised resource type.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (for `uvx`)
