# Design: resolving managed-identity principals and not-yet-existing resources

Status: **proposal** (for review — not yet implemented).

This document proposes how the tool should handle two related situations observed on
`terraform resolve principal_id from uami 2.plan`:

1. A role assignment whose `principal_id` is a **direct reference** to a sibling
   user-assigned identity (`azurerm_user_assigned_identity.X.principal_id`) is not
   resolved, even though we can look up the identity's principal id.
2. More fundamentally: the plan **creates** those identities (and the role
   assignments) — they do not exist in Azure yet. An `import {}` block only makes
   sense for a resource that already exists; importing a not-yet-existing resource
   makes `terraform apply` fail.

These are independent problems with independent fixes. Problem 1 is a resolver gap;
problem 2 is a policy question about what deserves an import block at all.

---

## Problem 1 — trace `principal_id` directly to a sibling UAMI

### Current behaviour

`_resolver_azurerm_role_assignment` (`resolvers.py`) can derive the principal's
managed identity **only** via `_resolve_ra_each` — the `for_each` over a
`role_assignments`-style map. A plain reference like

```hcl
principal_id = azurerm_user_assigned_identity.api_management_identity.principal_id
```

is not followed, so `principal_id` stays UNKNOWN and the resource lands in the
unresolved sink with:

```
principal_id (computed — references another resource)
  = azurerm_user_assigned_identity.api_management_identity.principal_id
```

### Proposed change

When `principal_id` is UNKNOWN and `_resolve_ra_each` did not already yield a
`uai_arm_id`, add a third path that traces the `principal_id` HCL expression to a
sibling `azurerm_user_assigned_identity` and builds its ARM id — reusing the
existing machinery:

- `_find_referenced_resource(changes, address, plan_file, "azurerm_role_assignment",
  "principal_id", "azurerm_user_assigned_identity")` to locate the identity change
  (same-module match, then cross-module `trace_reference`), and
- `build_id(uai, sub)` to construct its ARM id, exactly as `_resolve_ra_each` does at
  `resolvers.py:268-276`.

The existing live tail already does the rest: `az identity show --ids <uai> --query
principalId` then `az role assignment list …` (`resolvers.py:167-177`). No new `az`
command is introduced.

Scope for this assignment (`apim_kv_secret_user`) points at the key vault and is
already handled by `_resolve_scope_from_plan`.

### Why this alone does not finish the job

Even with this fix, the live lookups **correctly fail** when the identity does not
exist yet: `az identity show` returns nothing → no principal → still unresolved; and
if the principal were known, `az role assignment list` returns empty because the
assignment itself does not exist yet. So the assignment stays unresolved — which is
the right outcome, but the *label* ("unresolved") is misleading. That is problem 2.

---

## Problem 2 — do not emit imports for resources that do not exist yet

### The principle

`import {}` reconciles Terraform state with an **existing** Azure resource. A
genuinely new resource must be **created** by `terraform apply`, not imported.
An import block for a non-existent resource makes apply fail
(`Cannot import … resource does not exist`).

### Where it actually bites

For the two role assignments in the sample plan the tool already emits **no** import
block — they carry a `<name>` placeholder and go to `.unresolved`. And, as noted
above, the live resolvers self-correct: a missing identity/assignment simply fails to
resolve. So role assignments are *safe* today; only their reason text is unhelpful.

The dangerous case is a resource with a **deterministic** ID —
subnet, storage account, key vault, etc. — resolved by the formula or a cross-plan
resolver. There the tool builds a complete ID and emits an import block
**unconditionally**, whether or not the resource exists. That is the block that
breaks `terraform apply` for a mixed plan (some resources new, some pre-existing).

### Proposed change: an existence gate

Add an optional verification step between "we have a fully-resolved ID" and "write
the import block":

- New flag `--verify-exists` (opt-in), **independent** of `--auto-resolve`
  (decision below). When set, before emitting a resolved import block, probe whether
  the resource exists.
- **Exists** → emit the import block as today.
- **Does not exist (404 / empty)** → do *not* emit an import block; route to the new
  `.pending` bucket with reason `resource does not exist yet — will be created by
  apply`.
- **Unknown / probe errored** → treat as today (emit), so a transient `az` failure
  never silently drops an import. Log the uncertainty.

This is deliberately opt-in: the tool's primary workflow (adopting existing infra) has
every resource already present, and an existence check per resource is extra latency.
`--verify-exists` is for mixed plans.

#### Per-type existence probes (decision: per-type, not ARM-only)

Most resolved IDs are ARM ids and are probed generically with
`az resource show --ids <id>`. IDs that are **not** ARM-addressable (Key Vault
data-plane URLs, azuread graph objects) get a type-specific probe. A small
`_EXISTS_PROBES` dict maps resource type → a `(id) -> bool` callable; the generic ARM
probe is the default when no entry exists.

| resource type / id shape | existence probe |
|---|---|
| any ARM id (`/subscriptions/…`) — default | `az resource show --ids <id>` → exists iff non-empty |
| `azurerm_key_vault_secret` (`https://<vault>/secrets/<name>`) | `az keyvault secret show --id <url>` |
| `azurerm_key_vault_certificate` (`https://<vault>/certificates/<name>`) | `az keyvault certificate show --id <url>` |
| `azurerm_role_assignment` | already self-verifying: `az role assignment list` returns empty when it does not exist, so it never emits a phantom import — no extra probe needed |
| `azuread_service_principal` (`<object-id>`) | `az ad sp show --id <object-id>` |
| `azuread_application` (`/applications/<object-id>`) | `az ad app show --id <object-id>` |

Notes:
- Probes go through `_az`, so a **positive** result is cached; negatives are not (see
  Cache section) and are re-probed each run.
- azuread graph reads can require extra permissions; if a probe errors (as opposed to
  cleanly reporting "not found"), fall back to the "unknown → emit" rule rather than
  dropping the import.
- The role-assignment live resolver already only returns an id when the assignment
  exists, so `--verify-exists` is a no-op there — listed for completeness.

### Output routing

Three sinks instead of two:

| sink | contents |
|---|---|
| `FILE` | fully-resolved imports for resources that exist (unchanged) |
| `FILE.unresolved` | could not derive an ID (unchanged meaning) |
| `FILE.pending` *(new)* | ID derivable but resource does not exist yet — no import needed |

Alternatively, fold "pending" into `.unresolved` with a distinct reason to avoid a new
file. Splitting is clearer (`.unresolved` = "tool could not figure it out" vs
`.pending` = "nothing to import, by design"), so it is the recommended option.

---

## Cache: can we remember whether a resource exists?

Yes, with a caveat. `ResolveCache` today stores **only successful, non-empty**
results (`cache.py:38`); negatives are intentionally not cached so a later run can
succeed once the resource is created. That is exactly the right behaviour for the
"exists later" workflow.

For existence, that means:

- **Positive existence** (resource found) — safe to cache; it will not become false
  during a normal adopt workflow.
- **Negative existence** (404) — do **not** cache persistently. Caching it would make
  the tool keep reporting "does not exist" on the very run after `terraform apply`
  created it. Re-probe each run instead (cheap relative to being wrong).

Concretely: keep the existence probe going through `_az`/`ResolveCache` unchanged (so
a positive `az resource show` is cached as a side effect), and simply **not** cache
the 404 — which the current cache already does not (it only stores non-empty output).
No tri-state cache is needed for the recommended design; a tri-state with TTL is only
worth it if we later want to cache negatives for speed.

---

## Is the unresolved file reloaded on re-run?

No. Only the resolved `FILE` is read back, by `_read_existing_resolved`
(`cli.py:73`): previously-resolved blocks are preserved and their addresses skipped —
that is the convergence mechanism. The `.unresolved` sidecar is **fully rewritten**
every run (`cli.py:336`) or deleted when nothing is unresolved (`cli.py:339`).

This is exactly what we want for problem 2. The natural workflow converges:

1. Run **before** apply on a mixed plan → new resources land in `.pending`, existing
   ones in `FILE`.
2. `terraform apply` creates the new resources (and imports the existing ones via
   `FILE`).
3. Run again → the once-pending resources now exist, resolve, and move into `FILE`;
   `.pending` shrinks or is removed. (In practice, after apply they are in state and
   `--skip-imported` drops them — so the second run is mostly a confirmation.)

The new `.pending` file follows the same rewrite-per-run rule as `.unresolved`.

---

## CLI surface

- `--verify-exists` — gate resolved imports behind an `az resource show` existence
  probe; route non-existent resources to `.pending`.

No change to default behaviour without the flag.

---

## Summary of code touch points

| change | location |
|---|---|
| Trace direct `principal_id` → sibling UAMI | `_resolver_azurerm_role_assignment`, `resolvers.py` |
| Existence probe helper (`_exists(arm_id) -> bool`) | `resolvers.py` (uses `_az`, cached positives) |
| `--verify-exists` flag + gate + `.pending` routing | `cli.py` (`main`, `_format_unresolved`/new formatter, flush block) |
| No cache schema change | `cache.py` unchanged (negatives already not stored) |
| Doc: existence behaviour + workflow | `docs/resolvers.md`, `docs/usage.md` |

## Decisions

1. **Separate `.pending` file** for resources with a derivable ID that do not exist
   yet — kept distinct from `.unresolved` (tool could not derive an ID).
2. **Per-type existence probes**: generic `az resource show --ids` for ARM ids, plus
   type-specific probes for non-ARM ids (see the probe table above).
3. **`--verify-exists` is independent** of `--auto-resolve`; existence verification is
   always an explicit opt-in.
