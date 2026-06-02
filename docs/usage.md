# generate_imports — usage

Generates Terraform `import {}` blocks for all **CREATE** resources in a binary `.plan` file.
No `terraform` CLI or provider plugins required — reads the plan ZIP directly.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Azure CLI (`az`) — required for live ID resolution of Entra ID resources

## Basic usage

```powershell
uv run generate_imports <plan_file>
```

Output goes to stdout (pipe to a file); prompts and status messages go to stderr.

```powershell
uv run generate_imports terraform.plan > imports.tf
```

## Flags

| flag | description |
|---|---|
| `--yes` | Accept all without prompting |
| `--no` | Reject all without prompting (dry-run) |
| `--no-resolve` | Skip Azure CLI lookups; use formula-based IDs only |
| `--skip-imported` | Skip resources that already have an `import {}` block in the config |
| `--list` | Print `address\tid` pairs instead of HCL import blocks |
| `--list --powershell` | Output a PowerShell array literal of resource addresses |
| `--target ADDR …` | Only process the specified resource addresses |
| `--debug` | Dump all decoded attributes per resource |

## Interactive flow

For each CREATE resource, the tool prompts based on what it can derive:

### Resource with a live resolver (Entra ID)

The tool shows the `az` command it intends to run and asks confirmation:

```
module.infrastructure.azuread_application.default["my-app"]
  [resolve] az ad app list --display-name 'my-app' --query [0].id -o tsv
Run?  y=yes  n=skip  always=all  never=skip all resolves  at=always azuread_application  nt=never azuread_application
> y
```

If the `az` call returns nothing, a follow-up prompt offers to continue with a placeholder ID.

### Resource with a complete formula-based ID

Emitted immediately — no prompt needed:

```hcl
import {
  to = module.infrastructure.azurerm_key_vault_secret.app_registration_client_id["my-app"]
  id = "https://kv-myorg-core-tst-we-01.vault.azure.net/secrets/my-app-clientid"
}
```

### Resource with an unresolvable ID (computed attribute)

When a required attribute is computed (references another resource) and no live resolver can fetch it:

```
module.infrastructure.azuread_service_principal.default["my-app"]
  id = "<object_id>"
Resolve not possible (client_id (computed — references another resource) = each.value.client_id (for_each = azuread_application.default)).
y=skip this unresolvable import  n=stop  always=skip all unresolvable imports  at=skip all unresolvable azuread_service_principal imports
> 
```

The `for_each` chain is extracted from the `tfconfig/` HCL files in the plan ZIP.

### Unsupported resource types

Some resource types cannot be imported by the Terraform provider. These emit a comment block without prompting:

```hcl
# import not supported for azuread_application_password:
# module.infrastructure.azuread_application_password.default["my-app"]
```

## How IDs are derived

1. **Live resolver** (azuread resources) — calls `az` CLI to look up the actual ID from Azure.
2. **Formula** (`_ID_FORMULAS` in `ids.py`) — constructs the ID from decoded plan attributes.
3. **Placeholder** — if required attributes are computed/unknown, shows `<attr_name>` and prompts.

See `docs/resolvers.md` for how to add a new resolver or formula.

## Workflow

```powershell
# 1. Generate plan
terraform plan -out terraform.plan

# 2. Generate import blocks (interactive)
uv run generate_imports terraform.plan > imports.tf

# 3. Fill in any remaining placeholder IDs, then re-plan and apply
terraform plan -out terraform.plan
terraform apply terraform.plan
```
