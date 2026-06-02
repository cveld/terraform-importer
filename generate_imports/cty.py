from __future__ import annotations

from typing import Any

import msgpack

UNKNOWN = object()


def _decode_cty(obj: Any) -> Any:
    if isinstance(obj, msgpack.ExtType):
        return UNKNOWN
    if isinstance(obj, dict):
        return {
            (k.decode() if isinstance(k, bytes) else k): _decode_cty(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_decode_cty(v) for v in obj]
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return obj


def decode_attrs(raw: bytes) -> dict[str, Any]:
    try:
        obj = msgpack.unpackb(raw, raw=True, strict_map_key=False)
        result = _decode_cty(obj)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}
