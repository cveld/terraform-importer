# generate-imports-from-plan

Generates Terraform `import {}` blocks directly from a binary `.plan` file — no `terraform` CLI, no provider plugins, no `terraform init` required.

## Why?

`terraform show -json` does not expose all planned attribute values — computed fields like resource names and resource group names are missing from the JSON output. This tool reads the raw msgpack-encoded attributes directly from the binary plan, giving access to all values needed to construct Azure resource IDs.

For resources whose IDs are assigned at creation time (Entra ID resources, Azure role assignments), the tool calls the Azure CLI to look up the real ID interactively.

Resources whose ID depends on another resource in the same plan (e.g. `azurerm_virtual_network_dns_servers` needs the VNet ID, subnet associations need the subnet ID) are resolved automatically by reading the plan's HCL references — no CLI call needed. The correct subscription ID is determined per resource by following the `azurerm` provider chain through the config, so plans that span multiple subscriptions get the right ID on each resource.

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

# 2. Generate import blocks. --out splits resolved blocks (imports.tf) from
#    unresolved ones (imports.tf.unresolved); re-running converges.
generate-imports-from-plan terraform.plan --out imports.tf

# 3. Fill in any placeholders from imports.tf.unresolved, move them into
#    imports.tf, then re-plan and apply
terraform plan -out terraform.plan
terraform apply terraform.plan
```

Without `--out`, import blocks go to stdout (pipe with `> imports.tf`); unresolved ones are emitted as commented blocks.

## Interactive flow

The tool prompts per resource based on what it can derive:

- **Complete formula-based ID** — emitted immediately, no prompt.
- **Cross-plan ID** (depends on another resource in the plan) — resolved and emitted automatically, no prompt.
- **Entra ID resource or Azure role assignment** — shows the `az` command it will run and asks confirmation.
- **Unresolvable ID** — shows which attribute is computed and its HCL reference chain; emitted to the unresolved sink (sidecar with `--out`, commented block otherwise).
- **Unsupported import** (e.g. `azuread_application_password`) — emits a comment block.

Use `--auto-resolve` for a fully autonomous run (runs every `az` lookup without asking), or `--dry-run` to skip `az` calls and resolve deterministically (formula + cross-plan) only.

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
| `--out FILE` | Write resolved blocks to `FILE`, unresolved to `FILE.unresolved`; re-running converges |
| `--auto-resolve` | Run every Azure CLI resolve automatically, without prompting |
| `--dry-run` | Skip Azure CLI calls; resolve with formula + cross-plan only (no prompts) |
| `--skip-imported` | Skip resources that already have an `import {}` block in the config |
| `--list` | Print `address\tid` pairs instead of HCL blocks |
| `--list --powershell` | Output a PowerShell array literal of resource addresses |
| `--target ADDR [ADDR ...]` | Only emit the specified addresses (other plan resources stay available as cross-plan resolution context) |
| `--debug` | Dump all decoded attributes per resource |

## Adding a resource type

See `docs/resolvers.md` for how to add a formula, a cross-plan resolver, or a live resolver.

Quick formula example — extend `_ID_FORMULAS` in `generate_imports/ids.py`:

```python
"azurerm_my_resource":
    lambda a, s: _arm(s, _str(a, "resource_group_name"),
                      "Microsoft.MyNamespace/myResources", _str(a, "name")),
```
