#!/usr/bin/env python3
"""
ble_kegscale_scanner.py
-----------------------
- Robust E4BE payload matching (no strict scan filter).
- Decodes accelerated flag, battery, temp, seq, weight_raw (u16), state code, checksum.
- Optional per-state calibration via --cal-file JSON:
    {
      "0x0100": {"a": -0.0016956, "b": 37.42},
      "0x0101": {"a": -0.0016956, "b": 37.05},
      "0x0102": {"a": -0.0016956, "b": 36.50}
    }
  Formula: kg = a * weight_raw_u16 + b   (using state-specific a/b if available)
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bleak import BleakScanner, AdvertisementData

E4BE_UUID_128 = "0000e4be-0000-1000-8000-00805f9b34fb"
E4BE_UUID_16  = "e4be"


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


def _find_e4be_payload(adv: AdvertisementData, want_uuid: str | None, debug: bool = False) -> bytes | None:
    if not adv.service_data:
        return None

    want = (want_uuid or "").lower()
    candidates = []
    for key, val in adv.service_data.items():
        k = str(key).lower()
        if want:
            if k == want or k.endswith(want) or want.endswith(k):
                candidates.append(val)
                continue
        if k == E4BE_UUID_128 or k.endswith(E4BE_UUID_128) or k == E4BE_UUID_16 or k.endswith(E4BE_UUID_16):
            candidates.append(val)

    for v in candidates:
        if isinstance(v, (bytes, bytearray)) and len(v) == 17:
            return bytes(v)

    for v in adv.service_data.values():
        if isinstance(v, (bytes, bytearray)) and len(v) == 17:
            if debug:
                sys.stderr.write("⚠️  Using 17-byte fallback (no UUID key match).\n")
            return bytes(v)

    return None


def load_calibration(path: str | None) -> dict[str, dict] | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        sys.stderr.write(f"⚠️ Calibration file not found: {path}\n")
        return None
    try:
        with open(p, "r") as f:
            data = json.load(f)
        # normalize keys
        out = {}
        for k, v in data.items():
            key = k.lower()
            if not key.startswith("0x"):
                key = f"0x{int(k):04x}"
            out[key] = {"a": float(v["a"]), "b": float(v["b"])}
        return out
    except Exception as e:
        sys.stderr.write(f"⚠️ Failed to load calibration file {path}: {e}\n")
        return None


def predict_weight_kg(raw: int, state_code: int, cal_map: dict[str, dict] | None) -> float | None:
    if cal_map is None:
        return None
    key_hex = f"0x{state_code:04x}"
    c = cal_map.get(key_hex)
    if not c:
        return None
    return c["a"] * float(raw) + c["b"]


async def main():
    parser = argparse.ArgumentParser(description="Scan and decode keg scale E4BE beacons with per-state calibration.")
    parser.add_argument("--mac", help="Filter for a specific MAC (case-insensitive).")
    parser.add_argument("--uuid", default=E4BE_UUID_128, help="Service UUID to parse (128- or 16-bit ok).")
    parser.add_argument("--log-file", help="Write NDJSON lines to this file.")
    parser.add_argument("--print-raw", action="store_true", help="Include raw Bleak advertisement fields in stderr.")
    parser.add_argument("--debug", action="store_true", help="Extra diagnostics to stderr.")
    parser.add_argument("--cal-file", help="Path to per-state calibration JSON.")
    args = parser.parse_args()

    mac_filter = args.mac.upper() if args.mac else None
    cal_map = load_calibration(args.cal_file)

    log_fp = open(args.log_file, "a", buffering=1) if args.log_file else None

    def detection_callback(device, adv: AdvertisementData):
        try:
            if mac_filter and (device.address or "").upper() != mac_filter:
                return

            payload = _find_e4be_payload(adv, args.uuid, debug=args.debug)
            if not payload:
                if args.debug and adv.service_data:
                    sys.stderr.write(f"no-match svc_data keys={list(adv.service_data.keys())}\n")
                return

            decoded = decode_payload(payload)

            kg_pred = predict_weight_kg(decoded.weight_raw_u16, decoded.state_code_u16, cal_map)

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
                "weight_kg": kg_pred,
                "state_code_u16": decoded.state_code_u16,
                "checksum_u8": decoded.checksum_u8,
            }

            print(json.dumps(record, separators=(",", ":")))

            if log_fp:
                log_fp.write(json.dumps(record, separators=(",", ":")) + "\n")
                log_fp.flush()

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
