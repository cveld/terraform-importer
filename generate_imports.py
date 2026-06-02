# /// script
# requires-python = ">=3.11"
# dependencies = ["msgpack"]
# ///
"""
Generate Terraform import blocks from a binary .plan file.

Reads CREATE resources directly from the protobuf inside the plan ZIP,
decodes the planned attribute values (msgpack/cty encoding), and constructs
the Azure resource ID per resource type — no terraform CLI or providers needed.

Resources that already have an import block in the config carry an 'importing'
field in the plan; use --skip-imported to omit those.

Proto field map (hashicorp/terraform internal/plans/planproto/planfile.proto):
  Plan                    field 3  -> repeated ResourceInstanceChange
  ResourceInstanceChange  field 13 -> addr   (string)
                          field 9  -> Change (embedded)
  Change                  field 1  -> action (varint: CREATE=1)
                          field 2  -> values (repeated DynamicValue; CREATE: [after])
                          field 5  -> Importing (embedded, optional)
  DynamicValue            field 1  -> msgpack (bytes, cty-encoded)
  Importing               field 1  -> id (string)

Usage:
  uv run generate_imports.py <plan_file>
  uv run generate_imports.py <plan_file> --skip-imported
  uv run generate_imports.py <plan_file> --list
  uv run generate_imports.py <plan_file> --list --powershell
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

import msgpack


# ---------------------------------------------------------------------------
# Protobuf action enum
# ---------------------------------------------------------------------------

class Action(IntEnum):
    NOOP               = 0
    CREATE             = 1
    READ               = 2
    UPDATE             = 3
    DELETE             = 5
    DELETE_THEN_CREATE = 6
    CREATE_THEN_DELETE = 7
    FORGET             = 8
    CREATE_THEN_FORGET = 9


# Sentinel for cty "unknown / computed after apply" values
UNKNOWN = object()


# ---------------------------------------------------------------------------
# Minimal protobuf wire-format reader
# ---------------------------------------------------------------------------

def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = data[pos]; pos += 1
        result |= (b & 0x7F) << shift
        shift += 7
        if not (b & 0x80):
            return result, pos


def _skip(data: bytes, pos: int, wire: int) -> int:
    if wire == 0:
        _, pos = _read_varint(data, pos)
    elif wire == 1:
        pos += 8
    elif wire == 2:
        n, pos = _read_varint(data, pos)
        pos += n
    elif wire == 5:
        pos += 4
    else:
        raise ValueError(f"Unknown protobuf wire type {wire}")
    return pos


def _fields(data: bytes, start: int, end: int):
    """Yield (field_number, wire_type, payload_start, payload_end)."""
    pos = start
    while pos < end:
        tag, pos = _read_varint(data, pos)
        field_num, wire = tag >> 3, tag & 7
        if wire == 2:
            length, pos = _read_varint(data, pos)
            yield field_num, wire, pos, pos + length
            pos += length
        else:
            s = pos
            pos = _skip(data, pos, wire)
            yield field_num, wire, s, pos


# ---------------------------------------------------------------------------
# cty msgpack decoder
# ---------------------------------------------------------------------------

def _decode_cty(obj: Any) -> Any:
    """Recursively decode a cty msgpack value to plain Python types."""
    if isinstance(obj, msgpack.ExtType):
        return UNKNOWN                          # computed / unknown after apply
    if isinstance(obj, dict):
        return {
            (k.decode() if isinstance(k, bytes) else k): _decode_cty(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_decode_cty(v) for v in obj]
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return obj


def decode_attrs(raw: bytes) -> dict[str, Any]:
    try:
        obj = msgpack.unpackb(raw, raw=True, strict_map_key=False)
        result = _decode_cty(obj)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Terraform plan parsers
# ---------------------------------------------------------------------------

@dataclass
class ResourceChange:
    address:     str
    action:      Action
    import_id:   str | None          # None = no importing field in plan
    after_attrs: dict[str, Any] = field(default_factory=dict)


def _parse_importing(data: bytes, start: int, end: int) -> str:
    for f, w, s, e in _fields(data, start, end):
        if f == 1 and w == 2:
            return data[s:e].decode()
    return ""


def _extract_dynamic_value_msgpack(data: bytes, start: int, end: int) -> bytes | None:
    """DynamicValue → raw msgpack bytes (field 1)."""
    for f, w, s, e in _fields(data, start, end):
        if f == 1 and w == 2:
            return data[s:e]
    return None


def _parse_change(data: bytes, start: int, end: int) -> tuple[Action, str | None, bytes | None]:
    """Returns (action, import_id | None, after_msgpack | None)."""
    action     = Action.NOOP
    import_id  = None
    dyn_values: list[bytes] = []

    for f, w, s, e in _fields(data, start, end):
        if f == 1 and w == 0:           # action (varint)
            raw, _ = _read_varint(data, s)
            try:
                action = Action(raw)
            except ValueError:
                pass
        elif f == 2 and w == 2:         # DynamicValue (repeated)
            mp = _extract_dynamic_value_msgpack(data, s, e)
            if mp is not None:
                dyn_values.append(mp)
        elif f == 5 and w == 2:         # Importing
            import_id = _parse_importing(data, s, e)

    # CREATE → one value = after state
    # UPDATE → two values = [before, after]
    after_mp: bytes | None = None
    if action == Action.CREATE and dyn_values:
        after_mp = dyn_values[0]
    elif action == Action.UPDATE and len(dyn_values) >= 2:
        after_mp = dyn_values[1]

    return action, import_id, after_mp


def _parse_resource_instance_change(data: bytes, start: int, end: int) -> ResourceChange | None:
    addr      = None
    action    = Action.NOOP
    import_id = None
    after_mp: bytes | None = None

    for f, w, s, e in _fields(data, start, end):
        if f == 13 and w == 2:          # addr
            addr = data[s:e].decode()
        elif f == 9 and w == 2:         # change
            action, import_id, after_mp = _parse_change(data, s, e)

    if not addr:
        return None

    after_attrs = decode_attrs(after_mp) if after_mp else {}
    return ResourceChange(address=addr, action=action, import_id=import_id,
                          after_attrs=after_attrs)


def parse_plan(data: bytes) -> list[ResourceChange]:
    changes: list[ResourceChange] = []
    for f, w, s, e in _fields(data, 0, len(data)):
        if f == 3 and w == 2:           # resource_changes
            rc = _parse_resource_instance_change(data, s, e)
            if rc:
                changes.append(rc)
    return changes


# ---------------------------------------------------------------------------
# Azure resource ID construction
# ---------------------------------------------------------------------------

_SUB_RE = re.compile(
    r"/subscriptions/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/",
    re.IGNORECASE,
)


def _scan_sub_id(obj: Any) -> str | None:
    """Recursively scan any attribute value for a subscription GUID."""
    if obj is UNKNOWN or obj is None:
        return None
    if isinstance(obj, str):
        m = _SUB_RE.search(obj)
        return m.group(1) if m else None
    if isinstance(obj, dict):
        for v in obj.values():
            result = _scan_sub_id(v)
            if result:
                return result
    if isinstance(obj, list):
        for v in obj:
            result = _scan_sub_id(v)
            if result:
                return result
    return None


def collect_subscription_id(changes: list[ResourceChange]) -> str | None:
    """Find a subscription ID from any resource's attributes."""
    for rc in changes:
        sub = rc.after_attrs.get("subscription_id")
        if isinstance(sub, str) and sub and sub is not UNKNOWN:
            return sub
        sub = _scan_sub_id(rc.after_attrs)
        if sub:
            return sub
    return None


def _str(attrs: dict, key: str) -> str:
    """Return a known string attribute value or a placeholder."""
    v = attrs.get(key)
    if v is None or v is UNKNOWN:
        return f"<{key}>"
    return str(v)


def _arm(sub: str, rg: str, *segments: str) -> str:
    return "/subscriptions/" + sub + "/resourceGroups/" + rg + "/providers/" + "/".join(segments)


# fmt: off
# (resource_type, formula(attrs, sub_id) -> str)
# Each formula must be safe to call even when some attributes are UNKNOWN;
# _str() returns a placeholder in that case so the output is still useful.
_ID_FORMULAS: dict[str, Any] = {
    # Core
    "azurerm_resource_group":
        lambda a, s: f"/subscriptions/{s}/resourceGroups/{_str(a,'name')}",
    "azurerm_management_group":
        lambda a, _: f"/providers/Microsoft.Management/managementGroups/{_str(a,'name')}",
    "azurerm_subscription":
        lambda a, s: f"/subscriptions/{_str(a,'subscription_id') if a.get('subscription_id') not in (None,UNKNOWN) else s}",

    # Identity
    "azurerm_user_assigned_identity":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.ManagedIdentity/userAssignedIdentities", _str(a,"name")),

    # Key Vault
    "azurerm_key_vault":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.KeyVault/vaults", _str(a,"name")),
    "azurerm_key_vault_access_policy":
        lambda a, _: f"{_str(a,'key_vault_id')}/objectId/{_str(a,'object_id')}",
    "azurerm_key_vault_certificate":
        lambda a, _: f"https://{_str(a,'key_vault_id').rsplit('/')[-1]}.vault.azure.net/certificates/{_str(a,'name')}",
    "azurerm_key_vault_secret":
        lambda a, _: f"https://{_str(a,'key_vault_id').rsplit('/')[-1]}.vault.azure.net/secrets/{_str(a,'name')}",

    # Storage
    "azurerm_storage_account":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Storage/storageAccounts", _str(a,"name")),
    "azurerm_storage_container":
        lambda a, s: f"/subscriptions/{s}/resourceGroups/{_str(a,'resource_group_name')}/providers/Microsoft.Storage/storageAccounts/{_str(a,'storage_account_name')}/blobServices/default/containers/{_str(a,'name')}",
    "azurerm_storage_blob":
        lambda a, s: f"https://{_str(a,'storage_account_name')}.blob.core.windows.net/{_str(a,'storage_container_name')}/{_str(a,'name')}",

    # App Service / Functions
    "azurerm_service_plan":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Web/serverFarms", _str(a,"name")),
    "azurerm_linux_function_app":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Web/sites", _str(a,"name")),
    "azurerm_windows_function_app":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Web/sites", _str(a,"name")),
    "azurerm_linux_web_app":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Web/sites", _str(a,"name")),
    "azurerm_windows_web_app":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Web/sites", _str(a,"name")),
    "azurerm_app_service_plan":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Web/serverFarms", _str(a,"name")),

    # EventGrid
    "azurerm_eventgrid_domain":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.EventGrid/domains", _str(a,"name")),
    "azurerm_eventgrid_topic":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.EventGrid/topics", _str(a,"name")),
    "azurerm_eventgrid_system_topic":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.EventGrid/systemTopics", _str(a,"name")),
    "azurerm_eventgrid_system_topic_event_subscription":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.EventGrid/systemTopics", _str(a,"system_topic"), "eventSubscriptions", _str(a,"name")),
    "azurerm_eventgrid_event_subscription":
        lambda a, s: f"{_str(a,'scope')}/providers/Microsoft.EventGrid/eventSubscriptions/{_str(a,'name')}",

    # Network
    "azurerm_virtual_network":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Network/virtualNetworks", _str(a,"name")),
    "azurerm_subnet":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Network/virtualNetworks", _str(a,"virtual_network_name"), "subnets", _str(a,"name")),
    "azurerm_network_security_group":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Network/networkSecurityGroups", _str(a,"name")),
    "azurerm_network_interface":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Network/networkInterfaces", _str(a,"name")),
    "azurerm_public_ip":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Network/publicIPAddresses", _str(a,"name")),
    "azurerm_private_endpoint":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Network/privateEndpoints", _str(a,"name")),
    "azurerm_private_dns_zone":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Network/privateDnsZones", _str(a,"name")),
    "azurerm_firewall":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Network/azureFirewalls", _str(a,"name")),
    "azurerm_firewall_policy":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Network/firewallPolicies", _str(a,"name")),

    # Container
    "azurerm_container_registry":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.ContainerRegistry/registries", _str(a,"name")),
    "azurerm_kubernetes_cluster":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.ContainerService/managedClusters", _str(a,"name")),

    # Service Bus
    "azurerm_servicebus_namespace":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.ServiceBus/namespaces", _str(a,"name")),
    "azurerm_servicebus_queue":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.ServiceBus/namespaces", _str(a,"namespace_name"), "queues", _str(a,"name")),
    "azurerm_servicebus_topic":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.ServiceBus/namespaces", _str(a,"namespace_name"), "topics", _str(a,"name")),
    "azurerm_servicebus_subscription":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.ServiceBus/namespaces", _str(a,"namespace_name"), "topics", _str(a,"topic_name"), "subscriptions", _str(a,"name")),

    # SQL
    "azurerm_mssql_server":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Sql/servers", _str(a,"name")),
    "azurerm_mssql_database":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Sql/servers",
            _str(a,"server_id").rsplit("/",1)[-1], "databases", _str(a,"name")),

    # Monitor
    "azurerm_log_analytics_workspace":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.OperationalInsights/workspaces", _str(a,"name")),
    "azurerm_monitor_diagnostic_setting":
        lambda a, _: f"{_str(a,'target_resource_id')}/providers/Microsoft.Insights/diagnosticSettings/{_str(a,'name')}",
    "azurerm_monitor_action_group":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Insights/actionGroups", _str(a,"name")),

    # RBAC
    "azurerm_role_assignment":
        lambda a, _: f"{_str(a,'scope')}/providers/Microsoft.Authorization/roleAssignments/{_str(a,'name')}",
    "azurerm_role_definition":
        lambda a, _: f"{_str(a,'scope')}|{_str(a,'role_definition_id')}",

    # Entra / AzureAD (azuread provider)
    "azuread_group":
        lambda a, _: _str(a, "object_id"),
    "azuread_application":
        lambda a, _: f"/applications/{_str(a,'object_id')}",
    "azuread_service_principal":
        lambda a, _: _str(a, "object_id"),
    "azuread_application_federated_identity_credential":
        lambda a, _: f"{_str(a,'application_id')}/federatedIdentityCredentials/{_str(a,'credential_id')}",
}
# fmt: on


def _resource_type(address: str) -> str:
    """Extract the resource type from a Terraform resource address.
    e.g. 'module.foo.module.bar.azurerm_rg.this["x"]' -> 'azurerm_rg'
    """
    addr = re.sub(r"\[.*?\]$", "", address)
    parts = addr.split(".")
    i = 0
    while i + 1 < len(parts) and parts[i] == "module":
        i += 2
    return parts[i] if i < len(parts) else address


def build_id(rc: ResourceChange, sub_id: str) -> tuple[str, bool]:
    """Return (id_string, is_derived). is_derived=False means type is unknown."""
    rtype   = _resource_type(rc.address)
    formula = _ID_FORMULAS.get(rtype)
    if formula:
        try:
            return formula(rc.after_attrs, sub_id), True
        except Exception:
            pass
    return "", False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def read_tfplan_bytes(plan_file: str) -> bytes:
    with zipfile.ZipFile(plan_file) as zf:
        names = zf.namelist()
        if "tfplan" not in names:
            raise SystemExit(
                f"ERROR: no 'tfplan' entry in {plan_file}\n"
                f"Entries found: {names}"
            )
        return zf.read("tfplan")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate Terraform import blocks from a binary .plan file."
    )
    ap.add_argument("plan_file", help="Path to the Terraform .plan file")
    ap.add_argument(
        "--skip-imported", action="store_true",
        help="Skip resources that already have an import block in the config",
    )
    ap.add_argument("--target", nargs="*", default=[], metavar="ADDR",
                    help="Only include these resource addresses")
    ap.add_argument("--list", action="store_true",
                    help="Print address/id pairs instead of import blocks")
    ap.add_argument("--powershell", action="store_true",
                    help="With --list: output a PowerShell array literal of addresses")
    ap.add_argument("--debug", action="store_true",
                    help="Dump decoded attributes for each CREATE resource")
    args = ap.parse_args()

    data    = read_tfplan_bytes(args.plan_file)
    changes = parse_plan(data)

    # Keep only CREATE resources
    changes = [c for c in changes if c.action == Action.CREATE]

    if args.skip_imported:
        changes = [c for c in changes if c.import_id is None]

    if args.target:
        changes = [c for c in changes if c.address in args.target]

    if not changes:
        print("# No matching CREATE resources found in this plan.", file=sys.stderr)
        return

    if args.debug:
        for c in changes:
            print(f"\n=== {c.address}")
            print(f"    action      : {c.action.name}")
            print(f"    import_id   : {c.import_id!r}")
            attrs = c.after_attrs
            if not attrs:
                print("    after_attrs : (empty — msgpack decode produced nothing)")
            else:
                for k, v in sorted(attrs.items()):
                    val_str = "<UNKNOWN>" if v is UNKNOWN else repr(v)
                    print(f"    {k:30s} = {val_str}")
        return

    sub_id = collect_subscription_id(changes) or "<subscription-id>"

    if args.list:
        if args.powershell:
            print("@(")
            for i, c in enumerate(changes):
                comma = "," if i < len(changes) - 1 else ""
                print(f'    "{c.address}"{comma}')
            print(")")
        else:
            for c in changes:
                import_id, _ = build_id(c, sub_id)
                print(f"{c.address}\t{import_id}")
        return

    for c in changes:
        import_id, derived = build_id(c, sub_id)
        suffix = "" if derived else "  # TODO: unknown resource type"
        print("import {")
        print(f"  to = {c.address}")
        print(f'  id = "{import_id}"{suffix}')
        print("}")


if __name__ == "__main__":
    main()
