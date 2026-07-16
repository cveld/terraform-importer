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
| `--no-cache` | Disable the persistent `az`-lookup cache |
| `--verify-exists` | Before emitting a resolved import block, probe Azure to confirm the resource exists. Resources that don't exist yet go to `FILE.pending` instead of getting a spurious import block. See [Verifying existence](#verifying-existence---verify-exists). |

### Resolve cache

Successful `az` lookups are cached, keyed by the exact `az` command. With `--out imports.tf` the cache is `imports.tf.cache`; without `--out` it falls back to `<plan>.resolve-cache.json`. Re-runs reuse cached results instead of calling Azure again — useful because the same lookup (e.g. a managed identity's principal ID) is often needed by several resources. Delete the file or pass `--no-cache` to force fresh lookups. The cache only stores successful, non-empty results.

### Resolve modes

There are three modes for how Azure CLI (`az`) lookups are handled. Everything else — formula-based IDs and deterministic cross-plan resolution — always runs regardless of mode.

| mode | cross-plan + formula | `az` CLI lookups | prompts |
|---|---|---|---|
| **interactive** (default) | yes | yes | asks before each resolve |
| `--auto-resolve` | yes | yes | none — runs them all |
| `--dry-run` | yes | no | none — deterministic IDs only |

Use `--auto-resolve` for a fully autonomous run, and `--dry-run` to preview what resolves deterministically without touching Azure.

### Verifying existence (`--verify-exists`)

An `import {}` block only makes sense for a resource that **already exists** in Azure — importing a resource the plan is about to *create* makes `terraform apply` fail (`Cannot import … resource does not exist`). This is only a risk in a **mixed** plan (some resources already exist, some are genuinely new).

With `--verify-exists`, the tool probes Azure for each fully-resolved ID before emitting it:

- **Exists** → the import block is written as usual.
- **Does not exist** → routed to **`FILE.pending`** (see [Writing to files](#writing-to-files-and-converging)) with a note; no import block is emitted.
- **Could not determine** (auth/transient `az` error) → emitted anyway, with a warning, so a probe failure never silently drops an import.

Most IDs are probed with `az resource show --ids`; non-ARM IDs (Key Vault data-plane URLs, azuread objects) use a type-specific probe. `azurerm_role_assignment` is already self-verifying (its resolver only returns an ID when the assignment exists), so it is not probed again.

The flag is independent of `--auto-resolve`; combine them for an autonomous run that also skips not-yet-existing resources. Positive existence results are cached; negatives are **not**, so a resource created by a later `apply` is re-probed and picked up on the next run.

## Writing to files (and converging)

`--out FILE` writes the generated import blocks to a file instead of stdout:

```powershell
uv run generate-imports-from-plan terraform.plan --out imports.tf --auto-resolve
```

Output is split across two files:

- **`FILE`** (e.g. `imports.tf`) — only **fully resolved** import blocks. This file is directly usable by Terraform.
- **`FILE.unresolved`** (e.g. `imports.tf.unresolved`) — resources whose ID still contains a `<placeholder>`, plus types that can't be imported. Each block is preceded by a comment explaining *why* it couldn't be resolved. The `.unresolved` suffix means Terraform does **not** read this file (it only picks up `*.tf`), so a partial run never breaks `terraform plan`.
- **`FILE.pending`** (e.g. `imports.tf.pending`) — only with `--verify-exists`: resources whose ID resolved fully but that **do not exist in Azure yet**. These need no import (apply will create them); the file is informational. Like `.unresolved`, Terraform ignores it, and it is rewritten each run — so once a resource exists it moves into `FILE` on the next run.

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
