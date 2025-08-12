from __future__ import annotations
from typing import Dict, Any

def decode_e4be(payload: bytes) -> Dict[str, Any]:
    """Decode your device's Service Data payload for UUID 0xE4BE.

    Mapping (confirmed by live tests):
    - Temperature: byte[5] as deci-deg C (e.g., 0xD5 -> 21.3°C). **One byte only.**
    - Battery (raw guess): byte[2].
    - Weight (raw): 32-bit little-endian *signed* at bytes [13:17].
    """
    out: Dict[str, Any] = {}
    n = len(payload)

    # Temperature (byte 5): deci-degrees Celsius
    if n > 5:
        out["temp_c"] = payload[5] / 10.0

    # Battery (byte 2) — raw value
    if n > 2:
        out["battery_raw"] = payload[2]

    # Weight raw 32-bit little-endian signed at bytes 13..16 (slice [13:17])
    if n > 16:
        out["weight_raw"] = int.from_bytes(payload[13:17], "little", signed=True)

    return out

def linear_weight_kg(weight_raw: int, tare: int = 0, scale: float = -1.0) -> float:
    """Convert raw signed reading into kg using a linear model.
    Default scale=-1.0 reflects that raw decreases as real weight increases.
    Calibrate 'tare' and 'scale' from two known points.
    """
    return (tare - weight_raw) * scale
