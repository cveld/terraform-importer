from __future__ import annotations

import pytest

from generate_imports.cty import UNKNOWN
import generate_imports.resolvers as R
from generate_imports.resolvers import (
    _find_sibling,
    _for_each_key,
    _match_target_change,
    _module_prefix,
    _resource_name,
    _resource_refs,
    _rtype_from_addr,
    get_resolver,
    resolve_cross_plan,
    resource_exists,
)

SUB = "11111111-1111-1111-1111-111111111111"


# ---------------------------------------------------------------------------
# address helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("address,expected", [
    ("module.infra.module.kv.azurerm_key_vault.kv", "module.infra.module.kv"),
    # for_each keys on intermediate modules are kept on purpose, so siblings
    # only match within the same module instance.
    ('module.infra.module.kv["core"].azurerm_x.n["k"]', 'module.infra.module.kv["core"]'),
    ("azurerm_resource_group.rg", ""),
])
def test_module_prefix(address, expected):
    assert _module_prefix(address) == expected


@pytest.mark.parametrize("address,expected", [
    ('module.m.azurerm_storage_container.sc["data"]', "azurerm_storage_container"),
    ("azurerm_resource_group.rg", "azurerm_resource_group"),
])
def test_rtype_from_addr(address, expected):
    assert _rtype_from_addr(address) == expected


def test_resource_name():
    assert _resource_name('module.m.azurerm_x.myname["k"]') == "myname"


@pytest.mark.parametrize("address,key", [
    ('azurerm_x.n["core"]', "core"),
    ("azurerm_x.n[0]", "0"),
    ("azurerm_x.n", None),
])
def test_for_each_key(address, key):
    assert _for_each_key(address) == key


# ---------------------------------------------------------------------------
# _resource_refs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expr,expected", [
    ("azurerm_app_configuration.conf[each.key].id", [("azurerm_app_configuration", "conf")]),
    ("azurerm_storage_account.sa.id", [("azurerm_storage_account", "sa")]),
    ("var.configs", []),                       # no underscore -> not a resource type
    ("data.azurerm_key_vault.ex.id", []),      # data. prefix rejected by lookbehind
    ("try(scope.id, scope)", []),              # no resource ref
    ("each.value.scope", []),                  # each.* is not a resource ref
])
def test_resource_refs(expr, expected):
    assert _resource_refs(expr) == expected


# ---------------------------------------------------------------------------
# _find_sibling
# ---------------------------------------------------------------------------

def test_find_sibling_single(change):
    sa = change("module.m.azurerm_storage_account.sa", name="acc")
    other = change("module.m.azurerm_resource_group.rg", name="rg")
    found, errors = _find_sibling([sa, other], "module.m.azurerm_storage_container.sc", "azurerm_storage_account")
    assert found is sa and errors == []


def test_find_sibling_disambiguated_by_key(change):
    sa1 = change('module.m.azurerm_storage_account.sa["a"]', name="a")
    sa2 = change('module.m.azurerm_storage_account.sa["b"]', name="b")
    found, errors = _find_sibling([sa1, sa2], 'module.m.azurerm_storage_container.sc["b"]', "azurerm_storage_account")
    assert found is sa2 and errors == []


def test_find_sibling_none(change):
    rg = change("module.m.azurerm_resource_group.rg", name="rg")
    found, errors = _find_sibling([rg], "module.m.azurerm_storage_container.sc", "azurerm_storage_account")
    assert found is None and errors


# ---------------------------------------------------------------------------
# cross-plan resolvers
# ---------------------------------------------------------------------------

def test_storage_container_cross_plan(change):
    sa = change("module.m.azurerm_storage_account.sa",
                name="stacc", resource_group_name="rg1", subscription_id=SUB)
    sc = change('module.m.azurerm_storage_container.sc["data"]',
                name="data", storage_account_id=UNKNOWN)
    id_, missing = resolve_cross_plan("azurerm_storage_container", sc.after_attrs,
                                      sc.address, [sa, sc], "")
    assert missing == []
    assert id_ == (f"/subscriptions/{SUB}/resourceGroups/rg1/providers"
                   "/Microsoft.Storage/storageAccounts/stacc"
                   "/blobServices/default/containers/data")


def test_storage_container_no_sibling(change):
    sc = change('module.m.azurerm_storage_container.sc["data"]',
                name="data", storage_account_id=UNKNOWN)
    id_, missing = resolve_cross_plan("azurerm_storage_container", sc.after_attrs,
                                      sc.address, [sc], "")
    assert id_ is None
    assert missing  # explains why


def test_key_vault_secret_cross_plan(change):
    kv = change("module.m.azurerm_key_vault.kv", name="myvault", resource_group_name="rg")
    sec = change('module.m.azurerm_key_vault_secret.s["x"]', name="mysecret", key_vault_id=UNKNOWN)
    id_, missing = resolve_cross_plan("azurerm_key_vault_secret", sec.after_attrs,
                                      sec.address, [kv, sec], "")
    assert missing == []
    assert id_ == "https://myvault.vault.azure.net/secrets/mysecret"


def test_vm_extension_cross_plan(change):
    vm = change("module.m.azurerm_linux_virtual_machine.vm",
                name="vm-01", resource_group_name="rg", subscription_id=SUB)
    ext = change('module.m.azurerm_virtual_machine_extension.ext["AADLogin"]',
                 name="AADLoginForLinux", virtual_machine_id=UNKNOWN)
    id_, missing = resolve_cross_plan("azurerm_virtual_machine_extension",
                                      ext.after_attrs, ext.address, [vm, ext], "")
    assert missing == []
    assert id_ == (f"/subscriptions/{SUB}/resourceGroups/rg/providers"
                   "/Microsoft.Compute/virtualMachines/vm-01"
                   "/extensions/AADLoginForLinux")


def test_vm_extension_no_sibling(change):
    ext = change('module.m.azurerm_virtual_machine_extension.ext["x"]',
                 name="AADLoginForLinux", virtual_machine_id=UNKNOWN)
    id_, missing = resolve_cross_plan("azurerm_virtual_machine_extension",
                                      ext.after_attrs, ext.address, [ext], "")
    assert id_ is None
    assert missing  # explains why


def test_app_service_certificate_binding_cross_plan(change):
    hostname_binding_id = (
        f"/subscriptions/{SUB}/resourceGroups/rg/providers/Microsoft.Web"
        "/sites/app1/hostNameBindings/admin.example.com"
    )
    cert = change("module.m.azurerm_app_service_managed_certificate.portal",
                  custom_hostname_binding_id=hostname_binding_id)
    binding = change("module.m.azurerm_app_service_certificate_binding.portal",
                     hostname_binding_id=hostname_binding_id,
                     certificate_id=UNKNOWN, ssl_state="SniEnabled")
    id_, missing = resolve_cross_plan("azurerm_app_service_certificate_binding",
                                      binding.after_attrs, binding.address,
                                      [cert, binding], "")
    assert missing == []
    assert id_ == (
        f"{hostname_binding_id}|"
        f"/subscriptions/{SUB}/resourceGroups/rg/providers"
        "/Microsoft.Web/certificates/admin.example.com"
    )


def test_app_service_certificate_binding_no_sibling(change):
    binding = change("module.m.azurerm_app_service_certificate_binding.portal",
                     hostname_binding_id="/subscriptions/x/.../hostNameBindings/h",
                     certificate_id=UNKNOWN, ssl_state="SniEnabled")
    id_, missing = resolve_cross_plan("azurerm_app_service_certificate_binding",
                                      binding.after_attrs, binding.address,
                                      [binding], "")
    assert id_ is None
    assert missing  # explains why


def test_no_cross_plan_resolver_registered(change):
    rc = change("azurerm_resource_group.rg", name="rg")
    id_, missing = resolve_cross_plan("azurerm_resource_group", rc.after_attrs, rc.address, [rc], "")
    assert id_ is None and missing == []


# ---------------------------------------------------------------------------
# live resolver: azurerm_role_assignment
# ---------------------------------------------------------------------------

def test_role_assignment_resolver_with_concrete_scope():
    attrs = {"scope": "/subscriptions/X/resourceGroups/rg",
             "principal_id": "pid-123", "role_definition_name": "Reader"}
    resolver, missing = get_resolver("azurerm_role_assignment", attrs)
    assert missing == []
    assert resolver is not None
    desc, _execute = resolver
    assert "--assignee-object-id 'pid-123'" in desc
    assert "roleDefinitionName=='Reader'" in desc
    assert "--assignee " not in desc  # must not use the graph-lookup flag


def test_role_assignment_resolver_uses_role_definition_id_when_present():
    attrs = {"scope": "/subscriptions/X/resourceGroups/rg",
             "principal_id": "pid", "role_definition_id": "/.../roleDefinitions/abc"}
    resolver, missing = get_resolver("azurerm_role_assignment", attrs)
    assert missing == []
    desc, _ = resolver
    assert "roleDefinitionId=='/.../roleDefinitions/abc'" in desc


def test_role_assignment_unresolved_scope_without_context():
    attrs = {"scope": UNKNOWN, "principal_id": "pid", "role_definition_name": "Reader"}
    resolver, missing = get_resolver("azurerm_role_assignment", attrs)
    assert resolver is None
    assert any("scope" in m for m in missing)


# ---------------------------------------------------------------------------
# _match_target_change — links a chain-trace target to a plan change
# ---------------------------------------------------------------------------

def test_match_target_change_with_instance_key(change):
    kv = change('module.infra.module.kv["core"].azurerm_key_vault.keyvault', name="v")
    other = change('module.infra.module.kv["data"].azurerm_key_vault.keyvault', name="v2")
    target = ([("infra", None), ("kv", "core")], "azurerm_key_vault", "keyvault")
    assert _match_target_change([kv, other], target) is kv


def test_match_target_change_none_key_matches_any(change):
    rg = change("module.root.module.child.azurerm_resource_group.rg", name="r")
    target = ([("root", None), ("child", None)], "azurerm_resource_group", "rg")
    assert _match_target_change([rg], target) is rg


def test_match_target_change_no_match(change):
    kv = change('module.infra.module.kv["core"].azurerm_key_vault.keyvault', name="v")
    target = ([("infra", None), ("kv", "other")], "azurerm_key_vault", "keyvault")
    assert _match_target_change([kv], target) is None


# ---------------------------------------------------------------------------
# resource_exists — existence verification (--verify-exists)
# ---------------------------------------------------------------------------

ARM = "/subscriptions/x/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/v"


@pytest.mark.parametrize("import_id", ["", "<id>", "<scope>/roleAssignments/<name>"])
def test_resource_exists_placeholder_is_unknown(import_id):
    assert resource_exists("azurerm_key_vault", import_id, {}) is None


def test_resource_exists_present(monkeypatch):
    monkeypatch.setattr(R, "_az", lambda *a: (ARM, ""))
    assert resource_exists("azurerm_key_vault", ARM, {}) is True


def test_resource_exists_absent(monkeypatch):
    monkeypatch.setattr(R, "_az", lambda *a: ("", "(ResourceNotFound) ... was not found"))
    assert resource_exists("azurerm_key_vault", ARM, {}) is False


def test_resource_exists_other_error_is_unknown(monkeypatch):
    # An auth/transient failure must not be read as "absent".
    monkeypatch.setattr(R, "_az", lambda *a: ("", "AADSTS700016 authentication failed"))
    assert resource_exists("azurerm_key_vault", ARM, {}) is None


def test_resource_exists_role_assignment_skips_probe(monkeypatch):
    def boom(*a):
        raise AssertionError("role_assignment must not be probed")
    monkeypatch.setattr(R, "_az", boom)
    assert resource_exists("azurerm_role_assignment",
                           f"{ARM}/providers/Microsoft.Authorization/roleAssignments/g",
                           {}) is True


def test_resource_exists_uses_type_specific_probe(monkeypatch):
    calls = []
    monkeypatch.setattr(R, "_az", lambda *a: (calls.append(a) or ("id", "")))
    url = "https://v.vault.azure.net/secrets/s"
    assert resource_exists("azurerm_key_vault_secret", url, {}) is True
    assert calls[0][:3] == ("keyvault", "secret", "show")
    assert url in calls[0]
