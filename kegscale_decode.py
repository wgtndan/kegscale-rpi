from __future__ import annotations
from typing import Dict, Any

def decode_e4be(payload: bytes) -> Dict[str, Any]:
    """Decode your device's Service Data payload for UUID 0xE4BE.

    Notes from investigation:
    - Temperature: bytes 5-6 as deci-deg C (e.g., 0x00E9 -> 23.3°C), endianness may be big per your logs.
    - Battery: likely byte 2 as a raw level (mapping to % needs confirmation).
    - Weight: 32-bit little-endian *signed* at bytes 13..16. Value decreases when weight is added (needs linear transform).
    """
    out: Dict[str, Any] = {}

    # Guard
    n = len(payload)

    # Temperature (bytes 5..6), adjust endianness if needed
    if n >= 7:
        temp_raw = int.from_bytes(payload[5:7], "big", signed=False)
        out["temp_c"] = temp_raw / 10.0

    # Battery (byte 2) — keep raw and simple passthrough; caller can map to %
    if n >= 3:
        out["battery_raw"] = payload[2]

    # Weight raw 32-bit little-endian signed at bytes 13..16
    if n >= 16:
        weight_raw = int.from_bytes(payload[12:16], "little", signed=True)
        out["weight_raw"] = weight_raw

    return out

def linear_weight_kg(weight_raw: int, tare: int = 0, scale: float = -1.0) -> float:
    """Convert raw signed reading into kg using a linear model.
    Default scale=-1.0 reflects that raw decreases as real weight increases.
    Calibrate 'tare' and 'scale' from two known points.
    """
    return (weight_raw - tare) * scale
