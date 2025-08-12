
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kegmaster_reader_formula.py
Bleak 1.0.x compatible, robust against short service-data frames.

Vendor mapping:
- Temp (°C): byte[5] / 10 (if present)
- Sequence:  byte[9] (if present)
- Raw Weight (u24 LE): bytes[13:16] (required for weight)
- Status (u16 LE): bytes[16:18] (if present)

Weight formula (no calibration):
    kg = (raw24 - 108801) * 0.00004296

Usage examples:
  Live with smoothing (recommended):
    python3 kegmaster_reader_formula.py --mac 5C:01:3B:35:92:EE --smooth 5 --print-raw

  Minimal (instantaneous only):
    python3 kegmaster_reader_formula.py --mac 5C:01:3B:35:92:EE
"""

import argparse
import asyncio
from collections import deque
from datetime import datetime
from bleak import BleakScanner

UUID_E4BE = "0000e4be-0000-1000-8000-00805f9b34fb"

def parse_payload(data: bytes):
    """Parse fields per vendor map. Return dict; tolerate short frames."""
    out = {
        "temp_c": None, "seq": None, "raw24": None, "status": None
    }
    # temp (byte 5)
    if len(data) > 5:
        out["temp_c"] = data[5] / 10.0
    # seq (byte 9)
    if len(data) > 9:
        out["seq"] = data[9]
    # raw24 (bytes 13..15)
    if len(data) > 15:
        b13, b14, b15 = data[13], data[14], data[15]
        out["raw24"] = b13 | (b14 << 8) | (b15 << 16)
    # status (bytes 16..17)
    if len(data) > 17:
        out["status"] = data[16] | (data[17] << 8)
    return out

def weight_from_raw24(raw24: int) -> float:
    return (raw24 - 108801) * 0.00004296

def make_callback(mac_target: str, uuid_filter: str, smooth_n: int, print_raw: bool):
    mac_norm = mac_target.replace(":", "").lower()
    suf = uuid_filter[-8:].lower() if uuid_filter else None
    window = deque(maxlen=max(1, smooth_n))

    def cb(device, adv):
        # MAC filter
        if device.address.replace(":", "").lower() != mac_norm:
            return

        svc = adv.service_data or {}
        for uuid, payload in svc.items():
            uuid_l = uuid.lower()
            if uuid_filter and not (uuid_l == uuid_filter.lower() or (suf and uuid_l.endswith(suf))):
                continue

            data = bytes(payload)
            fields = parse_payload(data)
            raw24 = fields["raw24"]
            if raw24 is None:
                # Not enough bytes in this packet; skip silently.
                continue

            kg_inst = weight_from_raw24(raw24)
            window.append(kg_inst)
            kg = sum(window) / len(window)

            # Build line
            line = f"{datetime.now().isoformat(timespec='seconds')} mac={device.address} rssi={adv.rssi} kg={kg:.3f} (inst={kg_inst:.3f}) raw24={raw24}"
            if fields["temp_c"] is not None:
                line += f" temp={fields['temp_c']:.1f}°C"
            if fields["seq"] is not None:
                line += f" seq={fields['seq']}"
            if fields["status"] is not None:
                line += f" status=0x{fields['status']:04x}"
            if print_raw:
                line += f" sd={data.hex()}"
            print(line)

    return cb

async def main():
    ap = argparse.ArgumentParser(description="Kegmaster live weight reader (fixed formula, no calibration)")
    ap.add_argument("--mac", required=True, help="Target MAC address")
    ap.add_argument("--uuid", default=UUID_E4BE, help="Service UUID filter (default E4BE)")
    ap.add_argument("--smooth", type=int, default=5, help="Rolling average window size")
    ap.add_argument("--print-raw", action="store_true", help="Print raw service-data hex")
    args = ap.parse_args()

    cb = make_callback(args.mac, args.uuid, args.smooth, args.print_raw)
    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    print("🔍 Listening for BLE advertisements... (Ctrl+C to stop)")
    try:
        while True:
            await asyncio.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        await scanner.stop()

if __name__ == "__main__":
    asyncio.run(main())
