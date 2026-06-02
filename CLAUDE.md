# terraform-importer

Generates Terraform `import {}` blocks from a binary `.plan` file — no `terraform` CLI or provider plugins needed.

## Entry point

`generate_imports.py` — run with `uv run generate_imports.py <plan_file>`

## Key design decisions

- The `.plan` file is a ZIP archive. Inside is `tfplan`, a raw protobuf binary.
- We parse the protobuf directly (field numbers in `docs/planfile-format.md`) instead of using `terraform show`, which requires provider plugins.
- Attribute values are decoded from cty msgpack payloads (`DynamicValue.msgpack`). Unknown/computed values appear as `msgpack.ExtType(code=0)` — use `msgpack.ExtType`, not `msgpack.Ext`.
- Azure resource IDs are constructed from decoded attributes using the formula table `_ID_FORMULAS`. Add new resource types there.

## Docs

- `docs/usage.md` — flags, workflow, how to add a resource type
- `docs/planfile-format.md` — ZIP layout, protobuf field map, cty msgpack encoding
