from __future__ import annotations

import subprocess
from typing import Callable

from .cty import UNKNOWN

Resolver = tuple[str, Callable[[], tuple[str, str]]]  # (description, executor)
ResolverResult = tuple[Resolver | None, list[str]]    # (resolver_or_None, missing_attrs)


def _az(*args: str) -> tuple[str, str]:
    """Run az CLI. Returns (stdout, stderr)."""
    import sys
    try:
        r = subprocess.run(
            ["az", *args],
            capture_output=True, text=True, check=True,
            shell=(sys.platform == "win32"),
        )
        return r.stdout.strip(), ""
    except subprocess.CalledProcessError as e:
        return "", e.stderr.strip()
    except FileNotFoundError:
        return "", "az CLI not found"


def _require(attrs: dict, *keys: str) -> tuple[dict[str, str], list[str]]:
    """Return (values, missing_descriptions). Descriptions distinguish computed vs absent attrs."""
    values, missing = {}, []
    for key in keys:
        raw = attrs.get(key)
        if raw is UNKNOWN:
            missing.append(f"{key} (computed — references another resource)")
        elif raw is None or raw == "":
            missing.append(f"{key} (not in plan)")
        else:
            values[key] = str(raw)
    return values, missing


def _resolver_azuread_service_principal(a: dict) -> ResolverResult:
    v, missing = _require(a, "client_id")
    if missing:
        return None, missing
    desc = f"az ad sp show --id {v['client_id']!r} --query id -o tsv"
    def execute() -> tuple[str, str]:
        return _az("ad", "sp", "show", "--id", v["client_id"], "--query", "id", "-o", "tsv")
    return (desc, execute), []


def _resolver_azuread_application(a: dict) -> ResolverResult:
    v, missing = _require(a, "display_name")
    if missing:
        return None, missing
    desc = f"az ad app list --display-name {v['display_name']!r} --query [0].id -o tsv"
    def execute() -> tuple[str, str]:
        oid, err = _az("ad", "app", "list", "--display-name", v["display_name"], "--query", "[0].id", "-o", "tsv")
        return (f"/applications/{oid}" if oid else ""), err
    return (desc, execute), []


def _resolver_azuread_app_role_assignment(a: dict) -> ResolverResult:
    v, missing = _require(a, "resource_object_id", "principal_object_id", "app_role_id")
    if missing:
        return None, missing
    desc = (f"az rest --method GET "
            f"--uri 'https://graph.microsoft.com/v1.0/servicePrincipals/{v['resource_object_id']}/appRoleAssignedTo' "
            f"--query \"value[?principalId=='{v['principal_object_id']}' && appRoleId=='{v['app_role_id']}'].id\" -o tsv")
    def execute() -> tuple[str, str]:
        assignment_id, err = _az(
            "rest", "--method", "GET",
            "--uri", f"https://graph.microsoft.com/v1.0/servicePrincipals/{v['resource_object_id']}/appRoleAssignedTo",
            "--query", f"value[?principalId=='{v['principal_object_id']}' && appRoleId=='{v['app_role_id']}'].id",
            "-o", "tsv",
        )
        return (f"/servicePrincipals/{v['resource_object_id']}/appRoleAssignedTo/{assignment_id}" if assignment_id else ""), err
    return (desc, execute), []


def _resolver_azuread_application_federated_identity_credential(a: dict) -> ResolverResult:
    v, missing = _require(a, "application_id", "display_name")
    if missing:
        return None, missing
    app_oid = v["application_id"].removeprefix("/applications/")
    desc = (f"az rest --method GET "
            f"--uri 'https://graph.microsoft.com/v1.0/applications/{app_oid}/federatedIdentityCredentials' "
            f"--query \"value[?name=='{v['display_name']}'].id\" -o tsv")
    def execute() -> tuple[str, str]:
        cred_id, err = _az(
            "rest", "--method", "GET",
            "--uri", f"https://graph.microsoft.com/v1.0/applications/{app_oid}/federatedIdentityCredentials",
            "--query", f"value[?name=='{v['display_name']}'].id",
            "-o", "tsv",
        )
        return (f"{app_oid}/federatedIdentityCredential/{cred_id}" if cred_id else ""), err
    return (desc, execute), []


_RESOLVERS: dict[str, Callable[[dict], Resolver | None]] = {
    "azuread_service_principal":                          _resolver_azuread_service_principal,
    "azuread_application":                                _resolver_azuread_application,
    "azuread_app_role_assignment":                        _resolver_azuread_app_role_assignment,
    "azuread_application_federated_identity_credential":  _resolver_azuread_application_federated_identity_credential,
}


def get_resolver(rtype: str, attrs: dict) -> tuple[Resolver | None, list[str]]:
    """Return (resolver_or_None, missing_attrs). missing_attrs is non-empty when attrs are unknown."""
    factory = _RESOLVERS.get(rtype)
    if not factory:
        return None, []
    try:
        return factory(attrs)
    except Exception:
        return None, []


def has_resolver(rtype: str) -> bool:
    """Return True if a resolver is registered for this resource type."""
    return rtype in _RESOLVERS
