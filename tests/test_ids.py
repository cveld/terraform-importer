from __future__ import annotations

from generate_imports.cty import UNKNOWN
from generate_imports.ids import build_id, collect_subscription_id, resource_type

SUB = "00000000-0000-0000-0000-000000000000"


def test_resource_group(change):
    rc = change("module.m.azurerm_resource_group.rg", name="myrg")
    id_, derived = build_id(rc, SUB)
    assert derived
    assert id_ == f"/subscriptions/{SUB}/resourceGroups/myrg"


def test_storage_account(change):
    rc = change("azurerm_storage_account.sa", name="stacc", resource_group_name="rg")
    id_, _ = build_id(rc, SUB)
    assert id_ == (f"/subscriptions/{SUB}/resourceGroups/rg/providers"
                   "/Microsoft.Storage/storageAccounts/stacc")


def test_app_configuration(change):
    rc = change("azurerm_app_configuration.c", name="appcs", resource_group_name="rg")
    id_, derived = build_id(rc, SUB)
    assert derived
    assert id_.endswith("/providers/Microsoft.AppConfiguration/configurationStores/appcs")


def test_cosmosdb_sql_container(change):
    rc = change("azurerm_cosmosdb_sql_container.c", name="cont",
                resource_group_name="rg", account_name="acc", database_name="db")
    id_, _ = build_id(rc, SUB)
    assert id_ == (f"/subscriptions/{SUB}/resourceGroups/rg/providers"
                   "/Microsoft.DocumentDB/databaseAccounts/acc"
                   "/sqlDatabases/db/containers/cont")


def test_dns_a_record(change):
    rc = change("azurerm_dns_a_record.a", name="www",
                resource_group_name="rg", zone_name="example.com")
    id_, _ = build_id(rc, SUB)
    assert id_ == (f"/subscriptions/{SUB}/resourceGroups/rg/providers"
                   "/Microsoft.Network/dnsZones/example.com/A/www")


def test_unknown_attr_becomes_placeholder(change):
    rc = change("azurerm_resource_group.rg", name=UNKNOWN)
    id_, derived = build_id(rc, SUB)
    assert derived
    assert "<name>" in id_


def test_unknown_type_not_derived(change):
    rc = change("azurerm_totally_made_up.x", name="y")
    id_, derived = build_id(rc, SUB)
    assert derived is False
    assert id_ == ""


def test_role_assignment_placeholder(change):
    rc = change("azurerm_role_assignment.r", scope=UNKNOWN, name=UNKNOWN)
    id_, derived = build_id(rc, SUB)
    assert derived
    assert id_ == "<scope>/providers/Microsoft.Authorization/roleAssignments/<name>"


def test_resource_type_strips_trailing_for_each_key():
    # The classic pitfall: nested module path with a trailing for_each key.
    assert resource_type('module.a.module.b["k"].azurerm_x.name["key"]') == "azurerm_x"


def test_resource_type_plain():
    assert resource_type("azurerm_resource_group.rg") == "azurerm_resource_group"


def test_collect_subscription_id_from_attr(change):
    rc = change("azurerm_x.y", subscription_id=SUB)
    assert collect_subscription_id([rc]) == SUB


def test_collect_subscription_id_scanned_from_arm_id(change):
    rc = change("azurerm_x.y", some_id=f"/subscriptions/{SUB}/resourceGroups/rg")
    assert collect_subscription_id([rc]) == SUB


def test_collect_subscription_id_none(change):
    rc = change("azurerm_x.y", name="z")
    assert collect_subscription_id([rc]) is None
