#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kegmaster_reader_auto_tare.py
Bleak 1.0.x compatible, with automatic tare functionality.

When started, the script will collect a few initial readings to determine
the tare value for the current session, assuming the scale is empty on startup.

Mapping (vendor-confirmed):
- Temp (°C): byte[5] / 10 (if present)
- Sequence:  byte[9] (if present)
- Raw Weight (int32 LE, signed): bytes[13:17]
- Status (u16 LE): bytes[16:18]  (optional)

Weight formula:
    kg = (TARE - RAW32) * SCALE

Default Scale Factor (from our calibration):
    SCALE = 7.171e-10

Usage:
  # Start with an empty scale for auto-taring:
  python3 kegmaster_reader_auto_tare.py --mac 5C:01:3B:35:92:EE --smooth 5

  # To skip auto-taring and provide a known tare value:
  python3 kegmaster_reader_auto_tare.py --mac ... --manual-tare -65133
"""

import argparse
import asyncio
from collections import deque
from datetime import datetime
from bleak import BleakScanner

UUID_E4BE = "0000e4be-0000-1000-8000-00805f9b34fb"

def select_entries(service_data: dict, uuid_filter: str | None):
    """Filter service data entries by UUID."""
    if not service_data:
        return []
    if not uuid_filter:
        return list(service_data.items())
    out = []
    suf = uuid_filter[-8:].lower()
    for k, v in service_data.items():
        kl = k.lower()
        if kl == uuid_filter.lower() or kl.endswith(suf):
            out.append((k, v))
    return out

def parse_fields(data: bytes):
    """Return dict with parsed fields. Tolerate short frames."""
    f = {"temp_c": None, "seq": None, "raw32": None, "status": None}
    if len(data) > 5:
        f["temp_c"] = data[5] / 10.0
    if len(data) > 9:
        f["seq"] = data[9]
    if len(data) > 16:
        f["raw32"] = int.from_bytes(data[13:17], "little", signed=True)
    if len(data) > 17:
        f["status"] = int.from_bytes(data[16:18], "little", signed=False)
    return f

def make_callback(mac_target: str, uuid_filter: str, scale: float, smooth_n: int, print_raw: bool, manual_tare: int | None, tare_samples: int):
    mac_norm = mac_target.replace(":", "").lower()
    window = deque(maxlen=max(1, smooth_n))

    # --- Auto-tare state variables ---
    dynamic_tare = manual_tare
    is_taring = (manual_tare is None)  # Only tare if a manual value isn't provided
    tare_readings = []
    # ---

    def cb(device, adv):
        nonlocal dynamic_tare, is_taring

        # MAC filter
        if device.address.replace(":", "").lower() != mac_norm:
            return

        svc = adv.service_data or {}
        for uuid, payload in select_entries(svc, uuid_filter):
            data = bytes(payload)
            f = parse_fields(data)
            raw32 = f["raw32"]
            if raw32 is None:
                continue  # skip short frames

            # --- AUTO-TARE LOGIC ---
            if is_taring:
                tare_readings.append(raw32)
                print(f"⏳ Taring... got reading {len(tare_readings)}/{tare_samples} (raw32={raw32})")
                if len(tare_readings) >= tare_samples:
                    # Taring complete: calculate median for robustness
                    tare_readings.sort()
                    dynamic_tare = tare_readings[len(tare_readings) // 2]
                    is_taring = False
                    print(f"✅ Tare complete! Using TARE = {dynamic_tare}")
                return # Skip weight calculation until taring is done
            # ---

            # --- NORMAL OPERATION ---
            kg_inst = (dynamic_tare - raw32) * scale
            window.append(kg_inst)
            kg = sum(window) / len(window)

            line = f"{datetime.now().isoformat(timespec='seconds')} mac={device.address} rssi={adv.rssi} kg={kg:.3f} (inst={kg_inst:.3f}) raw32={raw32}"
            if f["temp_c"] is not None:
                line += f" temp={f['temp_c']:.1f}°C"
            if f["seq"] is not None:
                line += f" seq={f['seq']}"
            if f["status"] is not None:
                line += f" status=0x{f['status']:04x}"
            if print_raw:
                line += f" sd={data.hex()}"
            print(line)

    return cb

async def main():
    ap = argparse.ArgumentParser(description="Kegmaster live weight reader with auto-tare.")
    ap.add_argument("--mac", required=True, help="Target MAC address")
    ap.add_argument("--uuid", default=UUID_E4BE, help="Service UUID filter (default E4BE)")
    ap.add_argument("--scale", type=float, default=7.171e-10, help="Scale factor (kg per raw unit)")
    ap.add_argument("--manual-tare", type=int, default=None, help="Manually provide a tare value to skip auto-taring.")
    ap.add_argument("--tare-samples", type=int, default=5, help="Number of samples to average for auto-taring.")
    ap.add_argument("--smooth", type=int, default=5, help="Rolling average packet window.")
    ap.add_argument("--print-raw", action="store_true", help="Print raw service data hex.")
    args = ap.parse_args()

    cb = make_callback(args.mac, args.uuid, args.scale, args.smooth, args.print_raw, args.manual_tare, args.tare_samples)
    scanner = BleakScanner(detection_callback=cb)

    print("🔍 Listening for BLE advertisements... (Ctrl+C to stop)")
    if args.manual_tare is None:
        print("⚖️  Place scale on a stable, empty surface for auto-taring.")
    await scanner.start()

    try:
        while True:
            await asyncio.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        await scanner.stop()

if __name__ == "__main__":
    asyncio.run(main())