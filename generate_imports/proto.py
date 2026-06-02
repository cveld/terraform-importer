from __future__ import annotations


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


def fields(data: bytes, start: int, end: int):
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


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    return _read_varint(data, pos)
