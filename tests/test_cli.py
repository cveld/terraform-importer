from __future__ import annotations

from generate_imports.cli import (
    _format_pending,
    _format_unresolved,
    _import_block,
    _read_existing_resolved,
)


def test_import_block_basic():
    assert _import_block("module.m.azurerm_x.n", "ID") == (
        'import {\n  to = module.m.azurerm_x.n\n  id = "ID"\n}'
    )


def test_import_block_suffix():
    block = _import_block("a.b", "ID", "  # TODO: unknown resource type")
    assert block.endswith('"ID"  # TODO: unknown resource type\n}')


def test_read_existing_resolved_keeps_only_complete_ids(tmp_path):
    p = tmp_path / "imports.tf"
    p.write_text(
        'import {\n  to = azurerm_resource_group.rg\n'
        '  id = "/subscriptions/x/resourceGroups/rg"\n}\n\n'
        'import {\n  to = azurerm_role_assignment.r\n'
        '  id = "<scope>/providers/Microsoft.Authorization/roleAssignments/<name>"\n}\n',
        encoding="utf-8",
    )
    blocks, addrs = _read_existing_resolved(p)
    assert addrs == {"azurerm_resource_group.rg"}      # placeholder block excluded
    assert len(blocks) == 1
    assert "resourceGroups/rg" in blocks[0]


def test_read_existing_resolved_missing_file(tmp_path):
    blocks, addrs = _read_existing_resolved(tmp_path / "does-not-exist.tf")
    assert blocks == [] and addrs == set()


def test_format_unresolved_sidecar_emits_live_block():
    items = [{"address": "a.b", "rtype": "azurerm_x", "id": "<id>",
              "reason": "computed", "supported": True}]
    out = _format_unresolved(items, commented=False)
    assert out.startswith("# unresolved (computed):")
    assert "import {" in out
    assert "#   to" not in out  # live HCL, only the reason line is a comment


def test_format_unresolved_stdout_comments_everything():
    items = [{"address": "a.b", "rtype": "azurerm_x", "id": "<id>",
              "reason": "computed", "supported": True}]
    out = _format_unresolved(items, commented=True)
    assert "#   to = a.b" in out
    assert '#   id = "<id>"' in out


def test_format_unresolved_unsupported():
    items = [{"address": "a.b", "rtype": "azuread_application_password", "supported": False}]
    out = _format_unresolved(items, commented=False)
    assert "# import not supported for azuread_application_password:" in out
    assert "# a.b" in out


def test_format_pending_sidecar_emits_live_block():
    items = [{"address": "a.b", "rtype": "azurerm_x", "id": "/subscriptions/x/foo",
              "reason": "resource does not exist yet — will be created by apply"}]
    out = _format_pending(items, commented=False)
    assert out.startswith("# pending (resource does not exist yet")
    assert 'id = "/subscriptions/x/foo"' in out
    assert "#   to" not in out  # live HCL, only the reason line is a comment


def test_format_pending_stdout_comments_everything():
    items = [{"address": "a.b", "rtype": "azurerm_x", "id": "/subscriptions/x/foo",
              "reason": "nope"}]
    out = _format_pending(items, commented=True)
    assert "#   to = a.b" in out
    assert '#   id = "/subscriptions/x/foo"' in out
