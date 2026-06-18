"""Tests for the cross-module reference chain tracer (config.trace_reference).

These use an injectable `read_module` so no .plan zip is needed. The fixtures
mirror the real shape of the integration plan:

    kv_existing: key_vault_id = var.vault.id
    infrastructure: module "kv_existing" { vault = local.keyvault_secrets_obj }
    infrastructure: local.keyvault_secrets_obj = { id = module.kv.core.vault.id }
    module kv:   output "vault" = azurerm_key_vault.keyvault
"""
from __future__ import annotations

from generate_imports.config import _module_context, _split_ref, trace_reference


def _infra_kv_reader(names):
    key = tuple(names)
    if key == ("infrastructure",):
        return [{
            "module": [{"kv_existing": {"vault": "${local.keyvault_secrets_obj}"}}],
            "locals": [{"keyvault_secrets_obj": {
                "id": "${module.kv.core.vault.id}", "use_existing": True}}],
        }]
    if key == ("infrastructure", "kv"):
        return [{"output": [{"vault": {"value": "${azurerm_key_vault.keyvault}"}}]}]
    return []


def test_split_ref_normalizes_indexing():
    assert _split_ref("var.vault.id") == ["var", "vault", "id"]
    assert _split_ref('${module.kv["core"].vault.id}') == ["module", "kv", "core", "vault", "id"]
    assert _split_ref("azurerm_key_vault.keyvault") == ["azurerm_key_vault", "keyvault"]


def test_module_context():
    addr = 'module.infrastructure.module.kv["core"].azurerm_key_vault.keyvault'
    assert _module_context(addr) == [("infrastructure", None), ("kv", "core")]
    assert _module_context("azurerm_resource_group.rg") == []


def test_trace_full_kv_chain():
    context = [("infrastructure", None), ("kv_existing", None)]
    target = trace_reference(_infra_kv_reader, context, _split_ref("var.vault.id"))
    assert target == ([("infrastructure", None), ("kv", "core")],
                      "azurerm_key_vault", "keyvault")


def test_trace_direct_resource_ref():
    target = trace_reference(lambda n: [], [("m", None)],
                             _split_ref("azurerm_storage_account.sa.id"))
    assert target == ([("m", None)], "azurerm_storage_account", "sa")


def test_trace_local_object_field():
    def reader(names):
        if tuple(names) == ("m",):
            return [{"locals": [{"obj": {"ref": "${azurerm_key_vault.kv.id}"}}]}]
        return []
    target = trace_reference(reader, [("m", None)], _split_ref("local.obj.ref"))
    assert target == ([("m", None)], "azurerm_key_vault", "kv")


def test_trace_module_output_without_instance_key():
    def reader(names):
        if tuple(names) == ("root", "child"):
            return [{"output": [{"out": {"value": "${azurerm_resource_group.rg}"}}]}]
        return []
    target = trace_reference(reader, [("root", None)], _split_ref("module.child.out.id"))
    assert target == ([("root", None), ("child", None)], "azurerm_resource_group", "rg")


def test_trace_unresolvable_returns_none():
    # var that the parent module does not assign
    target = trace_reference(lambda n: [], [("m", None)], _split_ref("var.missing.id"))
    assert target is None
