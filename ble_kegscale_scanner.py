#!/usr/bin/env python3
"""
kegscale_scanner.py  (robust matching)
-------------------------------------
Fixes:
- Removed strict scanner filter (service_uuids=[...]) which can drop packets
  when devices don't include the Service UUID list in their ADV.
- More robust matching of service_data keys (case-insensitive, 16-bit/128-bit).
- Fallback: if no explicit match, accept any 17-byte service_data value.

Decodes:
- Accelerated flag (button-press fast advertising)
- Battery percentage
- Temperature (°C)
- Sequence/counter
- Weight raw (u16) + optional linear calibration to kg
- State/range code (u16)

Usage:
  pip3 install bleak
  python3 kegscale_scanner.py --mac 5C:01:3B:35:92:EE --log-file beacons.ndjson --debug
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from bleak import BleakScanner, AdvertisementData

E4BE_UUID_128 = "0000e4be-0000-1000-8000-00805f9b34fb"
E4BE_UUID_16  = "e4be"


@dataclass
class WeightCal:
    """Linear calibration: kg = a * raw + b"""
    a: float = 0.0
    b: float = 0.0


@dataclass
class DecodedFrame:
    accelerated: bool
    battery_pct: int
    temp_c: float
    seq: int
    weight_raw_u16: int
    state_code_u16: int
    checksum_u8: int
    raw_hex: str


def _u16be(b: bytes) -> int:
    return (b[0] << 8) | b[1]


def _u32be(b: bytes) -> int:
    return (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]


def decode_payload(payload: bytes) -> DecodedFrame:
    if len(payload) != 17:
        raise ValueError(f"Expected 17-byte payload, got {len(payload)}")

    b = payload
    accelerated = bool(b[0] & 0x01)   # 0x20 normal, 0x21 accelerated (bit0)
    battery_pct = b[3]                # byte 3
    temp_c = b[5] / 10.0              # byte 5 deci-C
    seq = _u32be(b[6:10])             # bytes 6..9 u32 BE
    weight_raw_u16 = _u16be(b[12:14]) # bytes 12..13 u16 BE
    state_code_u16 = _u16be(b[14:16]) # bytes 14..15 u16 BE
    checksum_u8 = b[16]               # byte 16

    return DecodedFrame(
        accelerated=accelerated,
        battery_pct=battery_pct,
        temp_c=temp_c,
        seq=seq,
        weight_raw_u16=weight_raw_u16,
        state_code_u16=state_code_u16,
        checksum_u8=checksum_u8,
        raw_hex=payload.hex(),
    )


def apply_weight_calibration(raw: int, cal: WeightCal | None) -> float | None:
    if cal is None:
        return None
    return cal.a * raw + cal.b


def ndjson_dump(fp, obj: dict):
    fp.write(json.dumps(obj, separators=(",", ":")) + "\n")
    fp.flush()


def _find_e4be_payload(adv: AdvertisementData, want_uuid: str | None, debug: bool = False) -> bytes | None:
    """Return the E4BE payload bytes from AdvertisementData.service_data, if present."""
    if not adv.service_data:
        return None

    # Normalize UUID strings
    want = (want_uuid or "").lower()
    # Accept both 128-bit and 16-bit representations for matching
    candidates = []
    for key, val in adv.service_data.items():
        k = str(key).lower()
        if want:
            if k == want or k.endswith(want) or want.endswith(k):
                candidates.append(val)
                continue
        # Generic acceptance for E4BE keys
        if k == E4BE_UUID_128 or k.endswith(E4BE_UUID_128) or k == E4BE_UUID_16 or k.endswith(E4BE_UUID_16):
            candidates.append(val)

    # Prefer any 17-byte payload among candidates
    for v in candidates:
        if isinstance(v, (bytes, bytearray)) and len(v) == 17:
            return bytes(v)

    # Fallback: search ANY 17-byte service_data value
    for v in adv.service_data.values():
        if isinstance(v, (bytes, bytearray)) and len(v) == 17:
            if debug:
                sys.stderr.write("⚠️  Using 17-byte fallback (no UUID key match).\n")
            return bytes(v)

    # Nothing matched
    return None


async def main():
    parser = argparse.ArgumentParser(description="Scan and decode keg scale E4BE beacons (robust).")
    parser.add_argument("--mac", help="Filter for a specific MAC (case-insensitive).")
    parser.add_argument("--uuid", default=E4BE_UUID_128, help="Service UUID to parse (128- or 16-bit ok).")
    parser.add_argument("--log-file", help="Write NDJSON lines to this file.")
    parser.add_argument("--print-raw", action="store_true", help="Include raw Bleak advertisement fields in stderr.")
    parser.add_argument("--debug", action="store_true", help="Extra diagnostics to stderr.")
    parser.add_argument("--cal-a", type=float, default=None, help="Weight calibration slope a (kg per raw).")
    parser.add_argument("--cal-b", type=float, default=None, help="Weight calibration intercept b (kg at raw=0).")
    args = parser.parse_args()

    mac_filter = args.mac.upper() if args.mac else None
    cal = None
    if args.cal_a is not None and args.cal_b is not None:
        cal = WeightCal(a=args.cal_a, b=args.cal_b)

    log_fp = open(args.log_file, "a", buffering=1) if args.log_file else None

    def detection_callback(device, adv: AdvertisementData):
        try:
            # Filter MAC if requested
            if mac_filter and (device.address or "").upper() != mac_filter:
                return

            payload = _find_e4be_payload(adv, args.uuid, debug=args.debug)
            if not payload:
                if args.debug and adv.service_data:
                    sys.stderr.write(f"no-match svc_data keys={list(adv.service_data.keys())}\n")
                return

            decoded = decode_payload(payload)

            now = datetime.now(timezone.utc).isoformat()
            record = {
                "ts_iso": now,
                "mac": device.address,
                "rssi": adv.rssi,
                "rssi_source": "adv",
                "uuid": args.uuid,
                "raw_hex": decoded.raw_hex,
                "len": len(payload),
                "accelerated": decoded.accelerated,
                "battery_pct": decoded.battery_pct,
                "temperature_c": decoded.temp_c,
                "seq": decoded.seq,
                "weight_raw_u16": decoded.weight_raw_u16,
                "weight_kg": apply_weight_calibration(decoded.weight_raw_u16, cal),
                "state_code_u16": decoded.state_code_u16,
                "checksum_u8": decoded.checksum_u8,
            }

            print(json.dumps(record, separators=(",", ":")))

            if log_fp:
                ndjson_dump(log_fp, record)

            if args.print_raw or args.debug:
                dbg = {
                    "local_name": adv.local_name,
                    "service_uuids": adv.service_uuids,
                    "tx_power": adv.tx_power,
                    "manufacturer_data_keys": list((adv.manufacturer_data or {}).keys()),
                }
                sys.stderr.write(json.dumps(dbg) + "\n")

        except Exception as e:
            if args.debug:
                sys.stderr.write(f"decode error: {e}\n")

    # IMPORTANT: no strict service_uuids filter here (some devices omit it)
    scanner = BleakScanner(detection_callback=detection_callback)

    print("🔍 Listening for BLE advertisements... (Ctrl+C to stop)")
    try:
        await scanner.start()
        while True:
            await asyncio.sleep(0.3)
    except KeyboardInterrupt:
        pass
    finally:
        await scanner.stop()
        if log_fp:
            log_fp.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
