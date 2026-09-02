# terraform-importer

Generates Terraform `import {}` blocks from a binary `.plan` file — no `terraform` CLI or provider plugins needed.

## Entry point

`generate_imports/cli.py` — run with `uv run generate-imports-from-plan <plan_file>`

(The console script is `generate-imports-from-plan`, defined in `pyproject.toml` `[project.scripts]` — not `generate_imports`.)

## Tests

`uv run --group dev pytest` — unit tests in `tests/` use synthetic in-memory fixtures (fake `ResourceChange` objects, inline HCL), no `.plan` file or live `az` needed.

## Module layout

| module | responsibility |
|---|---|
| `cli.py` | argument parsing, interactive confirm/resolve flow |
| `plan.py` | unzip `.plan`, parse protobuf, decode after_attrs |
| `ids.py` | formula-based ID construction (`_ID_FORMULAS`), `IMPORT_UNSUPPORTED` |
| `resolvers.py` | live ID lookup via `az` CLI (azuread) + cross-plan resolvers (derive ID from a sibling resource in the plan) |
| `config.py` | parse `tfconfig/` HCL (via python-hcl2): attribute chains + per-resource subscription ID via the provider chain |
| `cty.py` | decode cty msgpack payloads |
| `proto.py` | low-level protobuf field iterator |
| `cache.py` | persistent read-through cache for `az` lookups (`<out>.cache`, or `<plan>.resolve-cache.json` without `--out`) |

## Key design decisions

- The `.plan` file is a ZIP with `tfplan` (protobuf), `tfstate`, and `tfconfig/` (raw HCL).
- Protobuf is parsed directly (field numbers in `docs/planfile-format.md`) — no provider plugins needed.
- Unknown/computed values decode as `msgpack.ExtType(code=0)` — use `msgpack.ExtType`, not `msgpack.Ext`.
- ID derivation layers (see `docs/resolvers.md`): formula (`_ID_FORMULAS` in `ids.py`), cross-plan resolver (`_CROSS_PLAN_RESOLVERS` — sibling resource in the same plan), cross-module reference trace (`config.trace_reference` — follows `var`/`local`/module-output chains, incl. `for_each` `role_assignments` maps), and live resolver (`_RESOLVERS` — `az` CLI, results cached). Live `az` results are cached per plan; subscription IDs fall back to scanning tfstate.
- Resources with complete IDs emit without prompting. Only unresolvable IDs trigger a prompt.
- `--out FILE` splits output: resolved blocks → `FILE`, unresolved (placeholder/unsupported) → `FILE.unresolved` (a non-`.tf` sidecar Terraform ignores). Re-running converges — resolved blocks already in `FILE` are kept and skipped (no re-resolve). See `docs/usage.md`.
- In `cli.py`, `changes` is the full CREATE set (the pool for cross-plan sibling lookup + subscription-id detection); `targets` is the filtered emit list (`--skip-imported`/`--target`). Resolvers always receive `changes`, never `targets` — so a `--target`ed resource can still resolve its ID from a sibling that isn't itself targeted.
- `tfconfig/` HCL is parsed with `python-hcl2` to show the HCL expression chain when an attribute is computed, and to resolve the per-resource subscription ID via the provider chain (`get_subscription_id_for_resource`). A plan can span multiple subscriptions — never assume one global subscription ID.

## Known pitfalls

- `resource_type()` in `ids.py` uses `re.sub(r"\[[^\]]*\]$", ...)` — must use `[^\]]*` (not `.*?`) to strip only the trailing for_each key. Using `.*?` with `$` causes greedy matching from the first `[` in nested module paths like `module.foo["vnet"].resource.name["key"]`, silently returning the full address instead of the resource type.
- `tfconfig/` directory names use **dots** to join nested module names, not slashes: `module.infrastructure.module.rg` → `tfconfig/m-infrastructure.rg/` (matches the `Key` field in `tfconfig/modules.json`). for_each/count keys are stripped (all instances share one config dir).
- tfconfig `.tf` files often have **CRLF** line endings, which break python-hcl2's lexer. Always normalise `\r\n` → `\n` before `hcl2.loads` (done in `config.py:_read_module_hcl`).
- python-hcl2 leaves surrounding quotes on plain string literals (`'"value"'`) and wraps references in `${...}`. Normalise both (see `config.py:_str_val` / `_classify_expr`).
- Resources whose ID depends on a computed attribute referencing another resource in the plan (e.g. `subnet_id`, `virtual_network_id`, `key_vault_id`) are resolved by cross-plan resolvers in `resolvers.py`. Attributes referencing resources **outside** the plan still emit `<placeholder>`.

## Docs

- `docs/usage.md` — flags, interactive flow, workflow
- `docs/resolvers.md` — how live resolvers work, how to add one
- `docs/planfile-format.md` — ZIP layout, protobuf field map, cty msgpack encoding
