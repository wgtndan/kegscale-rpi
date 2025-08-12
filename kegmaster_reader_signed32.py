
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kegmaster_reader_signed32.py
Bleak 1.0.x compatible (uses BleakScanner(detection_callback=...)).

Mapping (vendor-confirmed):
- Temp (°C): byte[5] / 10 (if present)
- Sequence:  byte[9] (if present)
- Raw Weight (int32 LE, signed): bytes[13:17]  (b13 | b14<<8 | b15<<16 | b16<<24)
- Status (u16 LE): bytes[16:18]  (optional)

Weight formula (no calibration):
    kg = (TARE - RAW32) * SCALE

Defaults (from your latest note):
    TARE = 118_295
    SCALE = 0.000000045885

Usage:
  python3 kegmaster_reader_signed32.py --mac 5C:01:3B:35:92:EE --smooth 5 --print-raw
  # Optional overrides:
  python3 kegmaster_reader_signed32.py --mac ... --tare 118295 --scale 4.5885e-8
"""

import argparse
import asyncio
from collections import deque
from datetime import datetime
from bleak import BleakScanner

UUID_E4BE = "0000e4be-0000-1000-8000-00805f9b34fb"

def select_entries(service_data: dict, uuid_filter: str | None):
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
        # bytes 13..16 inclusive -> slice [13:17]
        f["raw32"] = int.from_bytes(data[13:17], "little", signed=True)
    if len(data) > 17:
        f["status"] = int.from_bytes(data[16:18], "little", signed=False)
    return f

def make_callback(mac_target: str, uuid_filter: str, tare: int, scale: float, smooth_n: int, print_raw: bool):
    mac_norm = mac_target.replace(":", "").lower()
    window = deque(maxlen=max(1, smooth_n))

    def cb(device, adv):
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

            kg_inst = (tare - raw32) * scale
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
    ap = argparse.ArgumentParser(description="Kegmaster live weight (signed int32 mapping, fixed formula)")
    ap.add_argument("--mac", required=True, help="Target MAC address")
    ap.add_argument("--uuid", default=UUID_E4BE, help="Service UUID filter (default E4BE)")
    ap.add_argument("--tare", type=int, default=118_295, help="Tare raw32 value (empty reading)")
    ap.add_argument("--scale", type=float, default=0.000000045885, help="Scale factor (kg per raw unit)")
    ap.add_argument("--smooth", type=int, default=5, help="Rolling average packet window")
    ap.add_argument("--print-raw", action="store_true", help="Print raw service data hex")
    args = ap.parse_args()

    cb = make_callback(args.mac, args.uuid, args.tare, args.scale, args.smooth, args.print_raw)
    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    print("🔍 Listening for BLE advertisements... (Ctrl+C to stop)")
    try:
        while True:
            await asyncio.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        await scanner.stop()

if __name__ == "__main__":
    asyncio.run(main())
