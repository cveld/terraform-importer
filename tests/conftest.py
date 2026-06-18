from __future__ import annotations

import pytest

from generate_imports.plan import Action, ResourceChange


@pytest.fixture
def change():
    """Factory for synthetic ResourceChange objects.

    Usage: change("module.m.azurerm_x.name", attr1=..., attr2=UNKNOWN)
    """
    def _make(address: str, *, action: Action = Action.CREATE,
              import_id: str | None = None, **attrs) -> ResourceChange:
        return ResourceChange(
            address=address, action=action, import_id=import_id,
            after_attrs=dict(attrs),
        )
    return _make
