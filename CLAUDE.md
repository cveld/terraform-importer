# terraform-importer

Generates Terraform `import {}` blocks from a binary `.plan` file — no `terraform` CLI or provider plugins needed.

## Entry point

`generate_imports/cli.py` — run with `uv run generate_imports <plan_file>`

## Module layout

| module | responsibility |
|---|---|
| `cli.py` | argument parsing, interactive confirm/resolve flow |
| `plan.py` | unzip `.plan`, parse protobuf, decode after_attrs |
| `ids.py` | formula-based ID construction (`_ID_FORMULAS`), `IMPORT_UNSUPPORTED` |
| `resolvers.py` | live ID lookup via `az` CLI for azuread resources |
| `config.py` | parse `tfconfig/` HCL (via python-hcl2) to show attribute chains |
| `cty.py` | decode cty msgpack payloads |
| `proto.py` | low-level protobuf field iterator |

## Key design decisions

- The `.plan` file is a ZIP with `tfplan` (protobuf), `tfstate`, and `tfconfig/` (raw HCL).
- Protobuf is parsed directly (field numbers in `docs/planfile-format.md`) — no provider plugins needed.
- Unknown/computed values decode as `msgpack.ExtType(code=0)` — use `msgpack.ExtType`, not `msgpack.Ext`.
- ID derivation has two layers: formula (`_ID_FORMULAS`) and live resolver (`resolvers.py`). See `docs/resolvers.md`.
- Resources with complete IDs emit without prompting. Only unresolvable IDs trigger a prompt.
- `tfconfig/` HCL is parsed with `python-hcl2` to show the HCL expression chain when an attribute is computed.

## Docs

- `docs/usage.md` — flags, interactive flow, workflow
- `docs/resolvers.md` — how live resolvers work, how to add one
- `docs/planfile-format.md` — ZIP layout, protobuf field map, cty msgpack encoding
