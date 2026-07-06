from __future__ import annotations

import re
from typing import Any

from .cty import UNKNOWN
from .plan import ResourceChange


_SUB_RE = re.compile(
    r"/subscriptions/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/",
    re.IGNORECASE,
)


def _scan_sub_id(obj: Any) -> str | None:
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
    for rc in changes:
        sub = rc.after_attrs.get("subscription_id")
        if isinstance(sub, str) and sub and sub is not UNKNOWN:
            return sub
        sub = _scan_sub_id(rc.after_attrs)
        if sub:
            return sub
    return None


def _str(attrs: dict, key: str) -> str:
    v = attrs.get(key)
    if v is None or v is UNKNOWN:
        return f"<{key}>"
    return str(v)


def _arm(sub: str, rg: str, *segments: str) -> str:
    return "/subscriptions/" + sub + "/resourceGroups/" + rg + "/providers/" + "/".join(segments)


# fmt: off
_ID_FORMULAS: dict[str, Any] = {
    # Core
    "azurerm_resource_group":
        lambda a, s: f"/subscriptions/{s}/resourceGroups/{_str(a,'name')}",
    "azurerm_management_group":
        lambda a, _: f"/providers/Microsoft.Management/managementGroups/{_str(a,'name')}",
    "azurerm_subscription":
        lambda a, s: f"/subscriptions/{_str(a,'subscription_id') if a.get('subscription_id') not in (None,UNKNOWN) else s}",
    "azurerm_resource_provider_registration":
        lambda a, s: f"/subscriptions/{s}/providers/{_str(a,'name')}",

    # Identity
    "azurerm_user_assigned_identity":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.ManagedIdentity/userAssignedIdentities", _str(a,"name")),
    
    # App Configuration
    "azurerm_app_configuration":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.AppConfiguration/configurationStores", _str(a,"name")),
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
    # DNS A Record
    "azurerm_dns_a_record":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), f"Microsoft.Network/dnsZones/{_str(a,'zone_name')}/A", _str(a,"name")),
    # Private DNS A Record
    "azurerm_private_dns_a_record":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), f"Microsoft.Network/privateDnsZones/{_str(a,'zone_name')}/A", _str(a,"name")),
    "azurerm_firewall":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Network/azureFirewalls", _str(a,"name")),
    "azurerm_firewall_policy":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Network/firewallPolicies", _str(a,"name")),
    "azurerm_route_table":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Network/routeTables", _str(a,"name")),
    "azurerm_route":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Network/routeTables", _str(a,"route_table_name"), "routes", _str(a,"name")),
    "azurerm_network_security_rule":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Network/networkSecurityGroups", _str(a,"network_security_group_name"), "securityRules", _str(a,"name")),
    "azurerm_subnet_route_table_association":
        lambda a, _: _str(a, "subnet_id"),
    "azurerm_subnet_network_security_group_association":
        lambda a, _: _str(a, "subnet_id"),
    "azurerm_virtual_network_dns_servers":
        lambda a, _: f"{_str(a,'virtual_network_id')}/dnsServers/default",
    "azurerm_virtual_hub_connection":
        lambda a, _: f"{_str(a,'virtual_hub_id')}/hubVirtualNetworkConnections/{_str(a,'name')}",
    "azurerm_private_dns_zone_virtual_network_link":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.Network/privateDnsZones", _str(a,"private_dns_zone_name"), "virtualNetworkLinks", _str(a,"name")),

    # CosmosDB
    "azurerm_cosmosdb_account":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.DocumentDB/databaseAccounts", _str(a,"name")),
    "azurerm_cosmosdb_sql_database":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.DocumentDB/databaseAccounts", _str(a,"account_name"), "sqlDatabases", _str(a,"name")),
    "azurerm_cosmosdb_sql_container":
        lambda a, s: _arm(s, _str(a,"resource_group_name"), "Microsoft.DocumentDB/databaseAccounts", _str(a,"account_name"), "sqlDatabases", _str(a,"database_name"), "containers", _str(a,"name")),

    # Compute
    "azurerm_virtual_machine_extension":
        lambda a, _: f"{_str(a,'virtual_machine_id')}/extensions/{_str(a,'name')}",

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
    "azuread_app_role_assignment":
        lambda a, _: f"/servicePrincipals/{_str(a,'resource_object_id')}/appRoleAssignments/{_str(a,'id')}",
}
# fmt: on

# Resource types this tool cannot emit an importable ID for, mapped to the reason.
# Membership (`rtype in IMPORT_UNSUPPORTED`) tests the keys; the value is a
# human-readable explanation shown in the unresolved output.
IMPORT_UNSUPPORTED: dict[str, str] = {
    "azuread_application_password":
        "the azuread provider does not support importing application passwords",
    "terraform_data":
        "its ID is generated at create time and cannot be derived from the plan",
}


def resource_type(address: str) -> str:
    addr = re.sub(r"\[[^\]]*\]$", "", address)
    parts = addr.split(".")
    i = 0
    while i + 1 < len(parts) and parts[i] == "module":
        i += 2
    return parts[i] if i < len(parts) else address


def build_id(rc: ResourceChange, sub_id: str) -> tuple[str, bool]:
    """Return (id_string, is_derived). is_derived=False means type is unknown."""
    rtype = resource_type(rc.address)
    formula = _ID_FORMULAS.get(rtype)
    if formula:
        try:
            return formula(rc.after_attrs, sub_id), True
        except Exception:
            pass
    return "", False
