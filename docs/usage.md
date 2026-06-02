# generate_imports.py — usage

Generates Terraform `import {}` blocks for all **CREATE** resources in a binary `.plan` file.
No `terraform` CLI or provider plugins required — reads the plan ZIP directly.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)

## Basic usage

```powershell
uv run generate_imports.py <plan_file>
```

Example output:

```hcl
import {
  to = module.core.azurerm_resource_group.this
  id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-myapp-dev-we-01"
}
```

## Flags

| flag | description |
|---|---|
| `--skip-imported` | Skip resources that already have an `import {}` block in the Terraform config (i.e. their plan entry already carries an `importing` field) |
| `--list` | Print `address\tid` pairs instead of HCL import blocks |
| `--list --powershell` | Output a PowerShell array literal of resource addresses |
| `--target ADDR [ADDR ...]` | Only process the specified resource addresses |
| `--debug` | Dump all decoded attributes per resource — useful when an ID shows a placeholder |

## How IDs are derived

The planned attribute values (e.g. `name`, `resource_group_name`) are decoded from the msgpack payload inside the plan's protobuf. The subscription ID is scanned from any attribute that contains an Azure resource path.

If a resource type is not in the built-in ID formula table, the `id` is left empty with a `# TODO` comment. Use `--debug` to see all available attributes for that resource.

### Adding a new resource type

Extend `_ID_FORMULAS` in `generate_imports.py`:

```python
"azurerm_my_resource":
    lambda a, s: _arm(s, _str(a, "resource_group_name"),
                      "Microsoft.MyNamespace/myResources", _str(a, "name")),
```

`_arm(sub, rg, *segments)` constructs `/subscriptions/{sub}/resourceGroups/{rg}/providers/{segments...}`.  
`_str(attrs, key)` returns the attribute value or `<key>` as a placeholder if unknown/missing.

## Workflow

```powershell
# 1. Generate plan
terraform plan -out terraform.plan

# 2. Generate import blocks
uv run generate_imports.py terraform.plan > imports.tf

# 3. Fill in any remaining TODO ids, then re-plan
terraform plan -out terraform.plan

# 4. Apply
terraform apply terraform.plan
```
