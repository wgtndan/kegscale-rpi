from __future__ import annotations
from typing import Dict, Any
import struct

def decode_e4be(payload: bytes) -> Dict[str, Any]:
    """Decode KegScale Service Data payload for UUID 0xE4BE.

    Complete mapping based on KegMaster app analysis:
    - Temperature: byte[5] as deci-deg C OR bytes[19:21] as centi-deg C
    - Battery: byte[2] (single byte) OR bytes[17:19] as millivolts
    - Weight (raw): 32-bit little-endian signed at bytes [13:17]
    """
    out: Dict[str, Any] = {}
    n = len(payload)

    # Temperature decoding - try both methods
    if n > 5:
        # Single byte temperature (deci-degrees Celsius)
        temp_raw_byte = payload[5]
        out["temp_c_byte"] = temp_raw_byte / 10.0
        out["temp_f_byte"] = celsius_to_fahrenheit(temp_raw_byte / 10.0)
    
    # Enhanced temperature decoding (2-byte, centi-degrees)
    if n >= 21:
        temp_raw_word = struct.unpack('<h', payload[19:21])[0]
        out["temp_raw_word"] = temp_raw_word
        temp_celsius = temp_raw_word / 100.0
        out["temp_c"] = temp_celsius
        out["temp_f"] = celsius_to_fahrenheit(temp_celsius)

    # Battery decoding - try both methods
    if n > 2:
        # Single byte battery (raw value)
        out["battery_raw_byte"] = payload[2]
    
    # Enhanced battery decoding (2-byte millivolts)
    if n >= 19:
        battery_mv = struct.unpack('<H', payload[17:19])[0]
        out["battery_mv"] = battery_mv
        out["battery_percentage"] = mv_to_battery_percentage(battery_mv)

    # Weight raw 32-bit little-endian signed at bytes 13..16 (slice [13:17])
    if n > 16:
        weight_raw = int.from_bytes(payload[13:17], "little", signed=True)
        out["weight_raw"] = weight_raw
        out["weight_grams"] = weight_raw
        out["weight_kg"] = weight_raw / 1000.0
        out["weight_pounds"] = weight_raw * 0.00220462

    return out

def mv_to_battery_percentage(millivolts: int) -> int:
    """
    Convert millivolt reading to battery percentage.
    Uses the exact lookup table from the KegMaster app.
    """
    battery_voltage_table = [
        3165, 3246, 3293, 3327, 3353, 3374, 3392, 3408, 3422, 3434,  # 0-9%
        3445, 3455, 3465, 3473, 3481, 3489, 3496, 3502, 3506, 3514,  # 10-19%
        3522, 3531, 3539, 3547, 3555, 3563, 3571, 3580, 3588, 3596,  # 20-29%
        3604, 3612, 3620, 3629, 3637, 3645, 3653, 3661, 3669, 3678,  # 30-39%
        3686, 3694, 3702, 3710, 3718, 3727, 3735, 3743, 3751, 3759,  # 40-49%
        3767, 3776, 3784, 3792, 3800, 3808, 3817, 3825, 3833, 3841,  # 50-59%
        3849, 3857, 3866, 3874, 3882, 3890, 3898, 3906, 3915, 3923,  # 60-69%
        3931, 3939, 3947, 3955, 3964, 3972, 3980, 3988, 3996, 4004,  # 70-79%
        4013, 4021, 4029, 4037, 4045, 4054, 4062, 4070, 4078, 4086,  # 80-89%
        4094, 4103, 4111, 4119, 4127, 4135, 4143, 4152, 4160, 4168   # 90-100%
    ]
    
    if millivolts < 3165:
        return 0
    
    for i, voltage in enumerate(battery_voltage_table):
        if millivolts < voltage:
            return i
    
    return 100

def celsius_to_fahrenheit(celsius: float) -> float:
    """Convert Celsius to Fahrenheit using KegMaster app formula."""
    return (9 * celsius / 5) + 32

def linear_weight_kg(weight_raw: int, tare: int = 0, scale: float = -1.0) -> float:
    """Convert raw signed reading into kg using a linear model.
    Default scale=-1.0 reflects that raw decreases as real weight increases.
    Calibrate 'tare' and 'scale' from two known points.
    """
    return (tare - weight_raw) * scale
