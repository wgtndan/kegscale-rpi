

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ble_kegscale_reader_noframe.py
Works with Bleak 1.0.x (uses scanner.register_callback). No frame filtering by default.

- Reads BLE advertising "service data" (E4BE UUID or any present)
- Optionally extracts a u16 field at --raw-index (LE/BE), with --raw-byte and --raw-shift
- Optional frame gating via --frame-index and --frame-value (but disabled by default)
- Prints battery/temp if indices are provided
- Rolling average with --smooth
"""

import argparse
import asyncio
from datetime import datetime
from collections import deque
from bleak import BleakScanner

def parse_args():
    p = argparse.ArgumentParser(description="BLE Kegscale reader (advert-mode, no frame filtering by default)")
    p.add_argument("--mac", required=True, help="Target MAC address (case-insensitive)")
    p.add_argument("--raw-index", type=int, default=-1, help="Start byte for u16 raw field (-1 disables extraction)")
    p.add_argument("--endian", choices=["little", "big"], default="little", help="Endianness for u16 raw field")
    p.add_argument("--raw-shift", type=int, default=0, help="Right-shift to apply to the raw value (e.g. 8)")
    p.add_argument("--raw-byte", choices=["none", "low", "high"], default="none",
                   help="Use only one byte from the u16 before shifting")
    p.add_argument("--batt-index", type=int, default=3, help="Byte index for battery raw (optional)")
    p.add_argument("--temp-index", type=int, default=5, help="Start byte for temperature u16 (deci-°C, LE)")
    p.add_argument("--frame-index", type=int, default=-1, help="Byte index that carries a page/frame id (-1 disables)")
    p.add_argument("--frame-value", type=lambda s: int(s, 0), default=None, help="Specific frame value to accept (e.g. 0xFE)")
    p.add_argument("--smooth", type=int, default=5, help="Rolling average window for raw value")
    p.add_argument("--print-raw", action="store_true", help="Also print the service-data hex payload(s)")
    return p.parse_args()

def ts():
    return datetime.now().isoformat(timespec="seconds")

def get_service_data(adv):
    # Return dict {uuid: bytes} (already provided by Bleak)
    return adv.service_data or {}

def extract_u16(data: bytes, idx: int, endian: str) -> int | None:
    if idx is None or idx < 0 or idx + 1 >= len(data):
        return None
    return int.from_bytes(data[idx:idx+2], endian, signed=False)

def apply_byte_select_and_shift(val: int | None, raw_byte: str, shift: int) -> int | None:
    if val is None:
        return None
    if raw_byte == "low":
        val = val & 0xFF
    elif raw_byte == "high":
        val = (val >> 8) & 0xFF
    if shift:
        val = val >> shift
    return val

async def main():
    args = parse_args()
    target_mac = args.mac.replace(":", "").lower()
    window = deque(maxlen=max(1, args.smooth))

    def callback(device, adv):
        # Filter by MAC
        if device.address.replace(":", "").lower() != target_mac:
            return

        svc = get_service_data(adv)
        if not svc:
            return

        # Iterate all service data entries (there can be several UUIDs)
        for uuid, payload in svc.items():
            data = bytes(payload)

            # Optional frame gating
            if args.frame_index >= 0 and args.frame_value is not None:
                if args.frame_index < len(data):
                    if data[args.frame_index] != args.frame_value:
                        continue
                else:
                    continue  # out of range -> skip

            # Raw extraction (optional)
            raw_u16 = extract_u16(data, args.raw_index, args.endian) if args.raw_index >= 0 else None
            raw_val = apply_byte_select_and_shift(raw_u16, args.raw_byte, args.raw_shift)

            if raw_val is not None:
                window.append(raw_val)
                avg_val = sum(window) / len(window)
            else:
                avg_val = None

            # Battery / Temp (optional helpers)
            batt = data[args.batt_index] if args.batt_index is not None and args.batt_index < len(data) else None
            temp_c = None
            if args.temp_index is not None and args.temp_index + 1 < len(data):
                temp_raw = int.from_bytes(data[args.temp_index:args.temp_index+2], "little", signed=False)
                temp_c = temp_raw / 10.0

            # Frame byte (if requested index in range)
            frame = None
            if args.frame_index >= 0 and args.frame_index < len(data):
                frame = data[args.frame_index]

            # Print
            hex_blob = data.hex() if args.print_raw else None
            rssi = adv.rssi  # Bleak 1.0.x provides RSSI on advertisement data
            line = f"{ts()} mac={device.address} rssi={rssi} uuid={uuid}"
            if frame is not None:
                line += f" frame=0x{frame:02x}"
            if args.raw_index >= 0:
                line += f" raw={raw_val} avg={avg_val:.1f}" if avg_val is not None else f" raw=None"
            if temp_c is not None:
                line += f" temp={temp_c:.1f}°C"
            if batt is not None:
                line += f" batt={batt}%"
            if hex_blob:
                line += f" sd={hex_blob}"
            print(line)

    scanner = BleakScanner()
    scanner.register_callback(callback)
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
