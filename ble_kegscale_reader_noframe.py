
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ble_kegscale_reader_noframe.py
Bleak 1.0.x compatible (BleakScanner(detection_callback=...)).
No frame filtering by default; optional via --frame-index/--frame-value.

Adds:
- --dump-u16 : print rolling u16le window list per advert for quick mapping
- Battery heuristic: if b[batt_index] > 100, try (byte - 155) clamped to 0..100
"""

import argparse
import asyncio
from datetime import datetime
from collections import deque
from bleak import BleakScanner

def parse_args():
    p = argparse.ArgumentParser(description="BLE Kegscale reader (advert-mode, no frame filtering by default)")
    p.add_argument("--mac", required=True, help="Target MAC address (case-insensitive)")
    p.add_argument("--uuid", default="0000e4be-0000-1000-8000-00805f9b34fb", help="Service UUID to select from service_data")
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
    p.add_argument("--print-raw", dest="print_raw", action="store_true", help="Also print the service-data hex payload(s)")
    p.add_argument("--dump-u16", dest="dump_u16", action="store_true", help="Print all u16le windows [i..i+1] per advert")
    return p.parse_args()

def ts():
    return datetime.now().isoformat(timespec="seconds")

def select_service_data(svc, uuid_filter):
    """Return list of (uuid, bytes) service data entries to consider (filter by UUID)."""
    if not svc:
        return []
    out = []
    suf = uuid_filter[-8:].lower() if uuid_filter else None
    for k, v in svc.items():
        kl = k.lower()
        if not uuid_filter or kl == uuid_filter.lower() or (suf and kl.endswith(suf)):
            out.append((k, v))
    return out

def extract_u16(data, idx, endian):
    if idx is None or idx < 0 or idx + 1 >= len(data):
        return None
    return int.from_bytes(data[idx:idx+2], endian, signed=False)

def apply_byte_select_and_shift(val, raw_byte, shift):
    if val is None:
        return None
    if raw_byte == "low":
        val = val & 0xFF
    elif raw_byte == "high":
        val = (val >> 8) & 0xFF
    if shift:
        val = val >> shift
    return val

def batt_percent_from_byte(b):
    if b is None:
        return None
    # Heuristic: vendor byte seems offset; try byte-155 if >100
    if b > 100:
        pct = max(0, min(100, b - 155))
        return pct
    return b

def u16_windows_le(data):
    return [int.from_bytes(data[i:i+2], "little", signed=False) for i in range(max(0, len(data)-1))]

async def main():
    args = parse_args()
    target_mac = args.mac.replace(":", "").lower()
    window = deque(maxlen=max(1, args.smooth))

    def callback(device, advertisement_data):
        # Filter by MAC
        if device.address.replace(":", "").lower() != target_mac:
            return

        svc = advertisement_data.service_data or {}
        entries = select_service_data(svc, args.uuid)
        if not entries:
            return

        for uuid, payload in entries:
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

            # Battery / Temp
            batt_raw = data[args.batt_index] if args.batt_index is not None and args.batt_index < len(data) else None
            batt_pct = batt_percent_from_byte(batt_raw)
            temp_c = None
            if args.temp_index is not None and args.temp_index + 1 < len(data):
                temp_raw = int.from_bytes(data[args.temp_index:args.temp_index+2], "little", signed=False)
                temp_c = temp_raw / 10.0

            # Frame byte (if requested index in range)
            frame = None
            if args.frame_index >= 0 and args.frame_index < len(data):
                frame = data[args.frame_index]

            # Dump u16 windows if asked
            u16_dump = ""
            if args.dump_u16:
                pairs = u16_windows_le(data)
                u16_parts = [f"{i:02d}={pairs[i]}" for i in range(len(pairs))]
                u16_dump = " u16le[" + ", ".join(u16_parts) + "]"

            # Print
            rssi = getattr(advertisement_data, "rssi", None)
            line = f"{ts()} mac={device.address} rssi={rssi} uuid={uuid}"
            if frame is not None:
                line += f" frame=0x{frame:02x}"
            if args.raw_index >= 0:
                line += f" raw={raw_val} avg={avg_val:.1f}" if avg_val is not None else f" raw=None"
            if temp_c is not None:
                line += f" temp={temp_c:.1f}°C"
            if batt_pct is not None:
                line += f" batt={batt_pct}%"
            if args.print_raw:
                line += f" sd={data.hex()}"
            line += u16_dump
            print(line)

    scanner = BleakScanner(detection_callback=callback)
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
