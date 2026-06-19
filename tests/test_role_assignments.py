"""Tests for the role_assignments for_each resolution (option A internals)."""
from __future__ import annotations

from generate_imports.config import _split_ref, resolve_value
from generate_imports.resolvers import _decompose_role_assignments, _ra_source_ref


# A faithful slice of the real local.role_assignments structure.
RA_MAP = {
    "mi-func": {
        "object_id": "${module.identity.func.config.principal_id}",
        "type": '"ServicePrincipal"',
        "roles": {
            '"Contributor"': {"scopes": {"cosmos_core": "${module.cosmosdb.core.account.id}"}},
            '"Storage Blob Data Owner"': {"scopes": {"sa": "${module.storage.account.id}"}},
        },
    },
    "spn-azdo": {
        "object_id": "${data.azurerm_client_config.current.object_id}",
        "type": '"ServicePrincipal"',
        "roles": {
            '"App Configuration Data Owner"': {"scopes": {"rg": "${module.rg.groups.core.id}"}},
        },
    },
}


def test_ra_source_ref_extracts_var():
    expr = '{for p in var.role_assignments : ...}'
    assert _ra_source_ref(expr) == "var.role_assignments"


def test_ra_source_ref_none():
    assert _ra_source_ref("[for x in [1,2,3] : x]") is None


def test_decompose_simple_key():
    # mi-func / Contributor / cosmos_core
    oid, scope = _decompose_role_assignments(RA_MAP, "mi-func-Contributor-cosmos_core")
    assert oid == "module.identity.func.config.principal_id"
    assert scope == "module.cosmosdb.core.account.id"


def test_decompose_role_with_spaces():
    # role "Storage Blob Data Owner" -> spaces become dashes in the key
    oid, scope = _decompose_role_assignments(
        RA_MAP, "mi-func-Storage-Blob-Data-Owner-sa")
    assert scope == "module.storage.account.id"


def test_decompose_principal_and_role_with_dashes():
    # principal "spn-azdo" already contains a dash; must still match
    oid, scope = _decompose_role_assignments(
        RA_MAP, "spn-azdo-App-Configuration-Data-Owner-rg")
    assert oid == "data.azurerm_client_config.current.object_id"
    assert scope == "module.rg.groups.core.id"


def test_decompose_no_match():
    assert _decompose_role_assignments(RA_MAP, "nope-nope-nope") is None


def test_resolve_value_var_through_local_to_map():
    """var.role_assignments (in rbac) -> local.role_assignments (in parent)."""
    def reader(names):
        if tuple(names) == ("infra",):
            return [{
                "module": [{"rbac": {"role_assignments": "${local.role_assignments}"}}],
                "locals": [{"role_assignments": RA_MAP}],
            }]
        return []

    context = [("infra", None), ("rbac", None)]
    value, ctx = resolve_value(reader, context, _split_ref("var.role_assignments"))
    assert value == RA_MAP
    assert ctx == [("infra", None)]  # the map's expressions live in the parent module
