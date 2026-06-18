# generate_imports — usage

Generates Terraform `import {}` blocks for all **CREATE** resources in a binary `.plan` file.
No `terraform` CLI or provider plugins required — reads the plan ZIP directly.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Azure CLI (`az`) — required for live ID resolution of Entra ID resources

## Basic usage

```powershell
uv run generate-imports-from-plan <plan_file>
```

Output goes to stdout (pipe to a file); prompts and status messages go to stderr.

```powershell
uv run generate-imports-from-plan terraform.plan > imports.tf
```

## Flags

| flag | description |
|---|---|
| `--out FILE` | Write resolved import blocks to `FILE` (default: stdout). See [Writing to files](#writing-to-files-and-converging). |
| `--auto-resolve` | Run every Azure CLI resolve automatically, without prompting |
| `--dry-run` | Skip Azure CLI calls; resolve with formula + cross-plan only (no prompts). Output files are still written. |
| `--skip-imported` | Skip resources that already have an `import {}` block in the config |
| `--list` | Print `address\tid` pairs instead of HCL import blocks |
| `--list --powershell` | Output a PowerShell array literal of resource addresses |
| `--target ADDR …` | Only emit the specified resource addresses. Other resources in the plan remain available as cross-plan resolution context (siblings, subscription ID), so a targeted resource still resolves correctly. |
| `--debug` | Dump all decoded attributes per resource |

### Resolve modes

There are three modes for how Azure CLI (`az`) lookups are handled. Everything else — formula-based IDs and deterministic cross-plan resolution — always runs regardless of mode.

| mode | cross-plan + formula | `az` CLI lookups | prompts |
|---|---|---|---|
| **interactive** (default) | yes | yes | asks before each resolve |
| `--auto-resolve` | yes | yes | none — runs them all |
| `--dry-run` | yes | no | none — deterministic IDs only |

Use `--auto-resolve` for a fully autonomous run, and `--dry-run` to preview what resolves deterministically without touching Azure.

## Writing to files (and converging)

`--out FILE` writes the generated import blocks to a file instead of stdout:

```powershell
uv run generate-imports-from-plan terraform.plan --out imports.tf --auto-resolve
```

Output is split across two files:

- **`FILE`** (e.g. `imports.tf`) — only **fully resolved** import blocks. This file is directly usable by Terraform.
- **`FILE.unresolved`** (e.g. `imports.tf.unresolved`) — resources whose ID still contains a `<placeholder>`, plus types that can't be imported. Each block is preceded by a comment explaining *why* it couldn't be resolved. The `.unresolved` suffix means Terraform does **not** read this file (it only picks up `*.tf`), so a partial run never breaks `terraform plan`.

To finish an unresolved import: fill in the `<placeholder>` and move the block into `imports.tf`.

### Convergence — safe to re-run

Re-running with the same `--out FILE` is **idempotent**:

- Blocks already present in `FILE` with a complete ID are **kept and skipped** — no Azure CLI call is made for them again.
- Only still-unresolved and newly-appeared resources are processed.
- `FILE.unresolved` is rewritten fresh each run, so it **shrinks** as you resolve placeholders (and is deleted once nothing is left).

This makes the workflow incremental: run, resolve a few placeholders, re-run — without re-resolving what's already done. Resources that have already been imported (applied) drop out of the plan's CREATE set on their own, so they never reappear.

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

If the `az` call returns nothing, the tool falls back to the formula-based ID. If that still contains a `<placeholder>`, the resource goes to the unresolved sink (see below).

### Resource with a complete formula-based ID

Emitted immediately — no prompt needed:

```hcl
import {
  to = module.infrastructure.azurerm_key_vault_secret.app_registration_client_id["my-app"]
  id = "https://kv-myorg-core-tst-we-01.vault.azure.net/secrets/my-app-clientid"
}
```

### Resource with an unresolvable ID (computed attribute)

When a required attribute is computed (references another resource) and no live resolver can fetch it, the resource is routed to the **unresolved sink** — no prompt. With `--out` it lands in `FILE.unresolved`; otherwise it is emitted as a commented block. A comment records why it couldn't be resolved:

```hcl
# unresolved (client_id (computed — references another resource) = each.value.client_id (for_each = azuread_application.default)):
import {
  to = module.infrastructure.azuread_service_principal.default["my-app"]
  id = "<object_id>"
}
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
3. **Placeholder** — if required attributes are computed/unknown, the ID keeps a `<attr_name>` marker and the resource goes to the unresolved sink.

See `docs/resolvers.md` for how to add a new resolver or formula.

## Workflow

```powershell
# 1. Generate plan
terraform plan -out terraform.plan

# 2. Generate import blocks (interactive); resolved -> imports.tf,
#    unresolved -> imports.tf.unresolved
uv run generate-imports-from-plan terraform.plan --out imports.tf

# 3. Fill in placeholders from imports.tf.unresolved, move them into
#    imports.tf, then re-plan and apply
terraform plan -out terraform.plan
terraform apply terraform.plan
```
