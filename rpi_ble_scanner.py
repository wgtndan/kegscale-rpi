#!/usr/bin/env python3
from __future__ import annotations
import argparse
import asyncio
import binascii
from collections import deque
from datetime import datetime
from typing import Dict, Any
from bleak import BleakScanner

# Import Android-style ScanRecord parser and device decoder
from ble_scanrecord import parse_scan_record
from kegscale_decode import decode_e4be, linear_weight_kg

UUID_E4BE = "0000e4be-0000-1000-8000-00805f9b34fb"
UUID_FEAA = "0000feaa-0000-1000-8000-00805f9b34fb"

DEFAULT_TARE = 118_295
DEFAULT_SCALE = 0.000000045885  # kg per raw unit

def _norm_mac(s: str) -> str:
    return s.replace(":", "").lower()

def _merge_service_data(adv_obj) -> Dict[str, bytes]:
    """Merge Bleak's service_data with Android-style parsed service_data from raw AD bytes."""
    merged: Dict[str, bytes] = {}
    # Start with Bleak's already-parsed dict
    for k, v in (adv_obj.service_data or {}).items():
        merged[str(k)] = bytes(v)

    # Android ScanRecord-equivalent parse from raw frames
    for attr in ("advertisement_bytes", "scan_response"):
        raw = getattr(adv_obj, attr, None)
        if raw:
            sr = parse_scan_record(bytes(raw))
            for u, payload in sr.service_data.items():  # keys are uuid.UUID
                merged[str(u)] = bytes(payload)
    return merged

def _extract_extra_fields(payload: bytes) -> Dict[str, Any]:
    """Extract optional fields (seq, status, marker) not covered by decode_e4be() yet."""
    out: Dict[str, Any] = {}
    if len(payload) > 9:
        out["seq"] = payload[9]
    if len(payload) > 12:
        out["marker12"] = payload[12]
    if len(payload) > 17:
        out["status"] = int.from_bytes(payload[16:18], "little", signed=False)
    return out

def make_callback(mac_target: str|None, uuid_filter: str|None, tare: int, scale: float, smooth_n: int, print_raw: bool):
    mac_norm = _norm_mac(mac_target) if mac_target else None
    window = deque(maxlen=max(1, smooth_n))

    def cb(device, adv):
        if mac_norm and _norm_mac(device.address) != mac_norm:
            return

        service_data = _merge_service_data(adv)
        # If a uuid filter is provided, restrict to it (supports suffix match)
        entries = []
        if uuid_filter:
            suf = uuid_filter[-8:].lower()
            for k, v in service_data.items():
                kl = k.lower()
                if kl == uuid_filter.lower() or kl.endswith(suf):
                    entries.append((k, v))
        else:
            entries = list(service_data.items())

        if not entries:
            return

        for uuid_str, payload in entries:
            ts = datetime.now().isoformat(timespec="seconds")
            if uuid_str.lower().endswith(UUID_E4BE[-8:]):  # your device
                decoded = decode_e4be(payload)  # temp_c, battery_raw, weight_raw
                # add extra fields for convenience
                decoded.update(_extract_extra_fields(payload))
                # compute kg if weight_raw present
                if "weight_raw" in decoded and decoded["weight_raw"] is not None:
                    kg_inst = linear_weight_kg(decoded["weight_raw"], tare, scale)
                    window.append(kg_inst)
                    avg_kg = sum(window) / len(window)
                else:
                    kg_inst = None
                    avg_kg = None

                parts = []
                if "temp_c" in decoded and decoded["temp_c"] is not None:
                    parts.append(f"temp_c={decoded['temp_c']:.1f}")
                if "seq" in decoded and decoded["seq"] is not None:
                    parts.append(f"seq={decoded['seq']}")
                if "battery_raw" in decoded and decoded["battery_raw"] is not None:
                    parts.append(f"battery_raw={decoded['battery_raw']}")
                if "marker12" in decoded and decoded["marker12"] is not None:
                    parts.append(f"marker12=0x{decoded['marker12']:02x}")
                if "status" in decoded and decoded["status"] is not None:
                    parts.append(f"status=0x{decoded['status']:04x}")
                if "weight_raw" in decoded and decoded["weight_raw"] is not None:
                    parts.append(f"weight_raw={decoded['weight_raw']}")
                if kg_inst is not None:
                    parts.append(f"weight_kg={kg_inst:.3f}")
                    if smooth_n > 1:
                        parts.append(f"avg_kg={avg_kg:.3f} (n={len(window)})")
                if print_raw:
                    parts.append(f"sd={binascii.hexlify(payload).decode()}")

                print(f"{ts} mac={device.address} rssi={adv.rssi} uuid={uuid_str} " + " ".join(parts))

            else:
                # other service data (e.g., FEAA) – show hex payload for inspection
                hexstr = binascii.hexlify(payload).decode()
                print(f"{ts} mac={device.address} rssi={adv.rssi} uuid={uuid_str} sd={hexstr}")

    return cb

async def main():
    ap = argparse.ArgumentParser(description="RPI BLE scanner using Android-style ScanRecord parsing + kegscale_decode.")
    ap.add_argument("--mac", help="Optional target MAC to filter (e.g., 5C:01:3B:35:92:EE)")
    ap.add_argument("--uuid", default=UUID_E4BE, help="Service UUID filter (default E4BE) or 'all'")
    ap.add_argument("--tare", type=int, default=DEFAULT_TARE, help="Tare raw32 baseline")
    ap.add_argument("--scale", type=float, default=DEFAULT_SCALE, help="Scale factor (kg per raw unit)")
    ap.add_argument("--smooth", type=int, default=5, help="Rolling average window for weight display")
    ap.add_argument("--print-raw", action="store_true", help="Print raw service data hex")
    ap.add_argument("--adapter", default="hci0", help="HCI adapter to use (e.g., hci0, hci1)")
    args = ap.parse_args()

    uuid_filter = None if args.uuid.lower() == "all" else args.uuid
    cb = make_callback(args.mac, uuid_filter, args.tare, args.scale, args.smooth, args.print_raw)
    scanner = BleakScanner(detection_callback=cb, adapter=args.adapter, scanning_mode="active")
    await scanner.start()
    print(f"🔍 rpi_ble_scanner.py (Android-path) listening on {args.adapter}... (Ctrl+C to stop)")
    try:
        while True:
            await asyncio.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        await scanner.stop()

if __name__ == "__main__":
    asyncio.run(main())
