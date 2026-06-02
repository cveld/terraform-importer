# generate-imports-from-plan

Generates Terraform `import {}` blocks directly from a binary `.plan` file — no `terraform` CLI, no provider plugins, no `terraform init` required.

## Why?

`terraform show -json` does not expose all planned attribute values — computed fields like resource names and resource group names are missing from the JSON output. This tool reads the raw msgpack-encoded attributes directly from the binary plan, giving access to all values needed to construct Azure resource IDs.

For resources whose IDs are assigned at creation time (Entra ID resources, Azure role assignments), the tool calls the Azure CLI to look up the real ID interactively.

## Installation

```sh
uvx generate-imports-from-plan terraform.plan
```

Or install permanently:

```sh
uv tool install generate-imports-from-plan
generate-imports-from-plan terraform.plan
```

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (for `uvx`)
- Azure CLI (`az`) — required for live resolution of Entra ID resources and Azure role assignments

## Workflow

```powershell
# 1. Generate plan
terraform plan -out terraform.plan

# 2. Generate import blocks interactively
generate-imports-from-plan terraform.plan > imports.tf

# 3. Fill in any remaining placeholder IDs, then re-plan and apply
terraform plan -out terraform.plan
terraform apply terraform.plan
```

## Interactive flow

The tool prompts per resource based on what it can derive:

- **Complete formula-based ID** — emitted immediately, no prompt.
- **Entra ID resource or Azure role assignment** — shows the `az` command it will run and asks confirmation.
- **Unresolvable ID** — shows which attribute is computed and its HCL reference chain; asks whether to skip.
- **Unsupported import** (e.g. `azuread_application_password`) — emits a comment block.

Use `--yes` to accept all without prompting, `--no` for a dry run.

## Output

```hcl
import {
  to = module.core.azurerm_resource_group.this
  id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-myapp-dev-we-01"
}

import {
  to = module.core.azuread_application.default["my-app"]
  id = "/applications/00000000-0000-0000-0000-000000000000"
}

# import not supported for azuread_application_password:
# module.core.azuread_application_password.default["my-app"]
```

## Flags

| Flag | Description |
|---|---|
| `--yes` | Accept all without prompting |
| `--no` | Reject all without prompting (dry-run) |
| `--no-resolve` | Skip Azure CLI lookups; use formula-based IDs only |
| `--skip-imported` | Skip resources that already have an `import {}` block in the config |
| `--list` | Print `address\tid` pairs instead of HCL blocks |
| `--list --powershell` | Output a PowerShell array literal of resource addresses |
| `--target ADDR [ADDR ...]` | Only process the specified resource addresses |
| `--debug` | Dump all decoded attributes per resource |

## Adding a resource type

See `docs/resolvers.md` for how to add a formula or a live resolver.

Quick formula example — extend `_ID_FORMULAS` in `generate_imports/ids.py`:

```python
"azurerm_my_resource":
    lambda a, s: _arm(s, _str(a, "resource_group_name"),
                      "Microsoft.MyNamespace/myResources", _str(a, "name")),
```
