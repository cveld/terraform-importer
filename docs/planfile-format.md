# Terraform Plan File Format

## ZIP structure

A `.plan` file is a ZIP archive:

| entry | contents |
|---|---|
| `tfplan` | protobuf binary — the plan itself, with evaluated attribute values |
| `tfconfig/` | raw HCL config as JSON (unresolved expressions, e.g. `${var.name}`) |
| `tfstate` | prior state JSON |

**Use `tfplan` for attribute values. `tfconfig` contains source expressions, not computed values.**

## Protobuf field map

Source: `hashicorp/terraform` → `internal/plans/planproto/planfile.proto`

```
Plan
  field  3 → repeated ResourceInstanceChange   (resource_changes)
  field 14 → string                            (terraform_version)

ResourceInstanceChange
  field  8 → string    provider
  field  9 → Change    (embedded)
  field 13 → string    addr  (full address, e.g. module.foo.azurerm_rg.this["key"])

Change
  field 1 → varint    action  (enum — see below)
  field 2 → repeated DynamicValue   values
              CREATE:  [after_value]
              UPDATE:  [before_value, after_value]
              DELETE:  [before_value]
  field 5 → Importing (embedded, optional)

DynamicValue
  field 1 → bytes    msgpack   (cty-encoded attribute values)

Importing
  field 1 → string   id
```

## Action enum

| value | name |
|---|---|
| 0 | NOOP |
| 1 | CREATE |
| 2 | READ |
| 3 | UPDATE |
| 5 | DELETE |
| 6 | DELETE\_THEN\_CREATE |
| 7 | CREATE\_THEN\_DELETE |
| 8 | FORGET |

## cty msgpack encoding

Terraform encodes resource attribute values as [msgpack](https://msgpack.org/) using the [go-cty](https://github.com/zclconf/go-cty) library.

| cty type | msgpack encoding |
|---|---|
| object | map with string keys |
| string | str (bytes when decoded with `raw=True`) |
| number | int or float |
| bool | bool |
| null | nil |
| unknown / computed | `ExtType(code=0, data=b'\x00')` |
| list / set / tuple | array |

> **Note:** use `msgpack.ExtType` in Python, **not** `msgpack.Ext` (does not exist).

## Why parse the binary directly?

`terraform show` and `terraform show -json` require the provider plugins to be available locally. This fails in CI or on machines that haven't run `terraform init`. The binary plan file is self-contained and can be parsed without any providers.
