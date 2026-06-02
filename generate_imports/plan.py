from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from .cty import decode_attrs
from .proto import fields, read_varint


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


@dataclass
class ResourceChange:
    address:     str
    action:      Action
    import_id:   str | None
    after_attrs: dict[str, Any] = field(default_factory=dict)


def _parse_importing(data: bytes, start: int, end: int) -> str:
    for f, w, s, e in fields(data, start, end):
        if f == 1 and w == 2:
            return data[s:e].decode()
    return ""


def _extract_dynamic_value_msgpack(data: bytes, start: int, end: int) -> bytes | None:
    for f, w, s, e in fields(data, start, end):
        if f == 1 and w == 2:
            return data[s:e]
    return None


def _parse_change(data: bytes, start: int, end: int) -> tuple[Action, str | None, bytes | None]:
    action: Action = Action.NOOP
    import_id: str | None = None
    dyn_values: list[bytes] = []

    for f, w, s, e in fields(data, start, end):
        if f == 1 and w == 0:
            raw, _ = read_varint(data, s)
            try:
                action = Action(raw)
            except ValueError:
                pass
        elif f == 2 and w == 2:
            mp = _extract_dynamic_value_msgpack(data, s, e)
            if mp is not None:
                dyn_values.append(mp)
        elif f == 5 and w == 2:
            import_id = _parse_importing(data, s, e)

    after_mp: bytes | None = None
    if action == Action.CREATE and dyn_values:
        after_mp = dyn_values[0]
    elif action == Action.UPDATE and len(dyn_values) >= 2:
        after_mp = dyn_values[1]

    return action, import_id, after_mp


def _parse_resource_instance_change(data: bytes, start: int, end: int) -> ResourceChange | None:
    addr: str | None = None
    action = Action.NOOP
    import_id: str | None = None
    after_mp: bytes | None = None

    for f, w, s, e in fields(data, start, end):
        if f == 13 and w == 2:
            addr = data[s:e].decode()
        elif f == 9 and w == 2:
            action, import_id, after_mp = _parse_change(data, s, e)

    if not addr:
        return None

    after_attrs = decode_attrs(after_mp) if after_mp else {}
    return ResourceChange(address=addr, action=action, import_id=import_id,
                          after_attrs=after_attrs)


def parse_plan(data: bytes) -> list[ResourceChange]:
    changes: list[ResourceChange] = []
    for f, w, s, e in fields(data, 0, len(data)):
        if f == 3 and w == 2:
            rc = _parse_resource_instance_change(data, s, e)
            if rc:
                changes.append(rc)
    return changes


def read_tfplan_bytes(plan_file: str) -> bytes:
    with zipfile.ZipFile(plan_file) as zf:
        names = zf.namelist()
        if "tfplan" not in names:
            raise SystemExit(
                f"ERROR: no 'tfplan' entry in {plan_file}\n"
                f"Entries found: {names}"
            )
        return zf.read("tfplan")
