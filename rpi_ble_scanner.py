#!/usr/bin/env python3
from __future__ import annotations
import asyncio
import binascii
import uuid
from datetime import datetime, timezone

from bleak import BleakScanner

# Local modules
try:
    from ble_scanrecord import parse_scan_record
except Exception as e:
    parse_scan_record = None

from kegscale_decode import decode_e4be, linear_weight_kg

EDDYSTONE_UUID = uuid.UUID("0000feaa-0000-1000-8000-00805f9b34fb")
E4BE_UUID      = uuid.UUID("0000e4be-0000-1000-8000-00805f9b34fb")  # your device

# Simple calibration (edit these to suit your setup)
TARE = 0
SCALE = -1.0  # raw decreases when weight increases

def _now_iso():
    return datetime.now(timezone.utc).isoformat()

def decode_service_data(service_data: dict) -> dict:
    out = {}
    for key, payload in service_data.items():
        # keys may be str UUIDs or UUID objects depending on backend
        try:
            svc_uuid = uuid.UUID(str(key))
        except Exception:
            continue
        if svc_uuid == E4BE_UUID:
            decoded = decode_e4be(payload)
            if "weight_raw" in decoded:
                decoded["weight_kg"] = round(linear_weight_kg(decoded["weight_raw"], TARE, SCALE), 3)
            out[str(svc_uuid)] = {**decoded, "raw_hex": binascii.hexlify(payload).decode()}
        elif svc_uuid == EDDYSTONE_UUID:
            out[str(svc_uuid)] = {"raw_hex": binascii.hexlify(payload).decode()}
    return out

def handle_adv(d, ad_data):
    # Prefer already-parsed data from Bleak
    service_data = ad_data.service_data or {}

    # If possible, also parse any raw bytes (implementation-dependent)
    # Some backends expose ad_data.advertisement_bytes or ad_data.scan_response
    if parse_scan_record is not None:
        for attr in ("advertisement_bytes", "scan_response"):
            raw = getattr(ad_data, attr, None)
            if raw:
                sr = parse_scan_record(raw)
                # Merge parsed service_data from raw with Bleak's parsed dict
                for k, v in sr.service_data.items():
                    service_data[str(k)] = v

    decoded = decode_service_data(service_data)
    if not decoded:
        return

    print(
        _now_iso(),
        "mac=", d.address,
        "rssi=", ad_data.rssi,
        "name=", ad_data.local_name or "",
        decoded
    )

async def main():
    scanner = BleakScanner(callback=handle_adv)  # Bleak >= 1.0
    await scanner.start()
    print("🔍 Listening for BLE advertisements... (Ctrl+C to stop)")
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        await scanner.stop()

if __name__ == "__main__":
    asyncio.run(main())
