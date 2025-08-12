#!/usr/bin/env python3
from __future__ import annotations
import argparse
import asyncio
import binascii
from collections import deque
from datetime import datetime
from bleak import BleakScanner

UUID_E4BE = "0000e4be-0000-1000-8000-00805f9b34fb"
UUID_FEAA = "0000feaa-0000-1000-8000-00805f9b34fb"

# Default formula (matches your confirmed mapping)
DEFAULT_TARE = 118_295
DEFAULT_SCALE = 0.000000045885  # kg per raw unit (negative slope handled by (tare - raw))

def _norm_mac(s: str) -> str:
    return s.replace(":", "").lower()

def select_entries(service_data: dict, uuid_filter: str | None):
    """Return list[(uuid_str, bytes)] tolerant of UUID keys or strings."""
    if not service_data:
        return []
    if not uuid_filter:
        return [(str(k), bytes(v)) for k, v in service_data.items()]
    out = []
    suf = uuid_filter[-8:].lower()
    for k, v in service_data.items():
        ks = str(k)
        kl = ks.lower()
        if kl == uuid_filter.lower() or kl.endswith(suf):
            out.append((ks, bytes(v)))
    return out

def parse_fields_e4be(data: bytes):
    """Parse known fields from the E4BE Service Data payload, tolerating short frames."""
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

def format_labeled(fields: dict, tare: int, scale: float, include_hex: str|bytes|None):
    parts = []
    temp_c = fields.get("temp_c")
    seq = fields.get("seq")
    raw32 = fields.get("raw32")
    status = fields.get("status")

    if temp_c is not None:
        parts.append(f"temp_c={temp_c:.1f}")
    if seq is not None:
        parts.append(f"seq={seq}")
    if raw32 is not None:
        kg_inst = (tare - raw32) * scale
        parts.append(f"raw32={raw32}")
        parts.append(f"weight_kg={kg_inst:.3f}")
    if status is not None:
        parts.append(f"status=0x{status:04x}")
    if include_hex is not None:
        if isinstance(include_hex, (bytes, bytearray)):
            hexstr = binascii.hexlify(include_hex).decode()
        else:
            hexstr = str(include_hex)
        parts.append(f"sd={hexstr}")
    return " ".join(parts)

def make_callback(mac_target: str|None, uuid_filter: str|None, tare: int, scale: float, smooth_n: int, print_raw: bool):
    mac_norm = _norm_mac(mac_target) if mac_target else None
    window = deque(maxlen=max(1, smooth_n))

    def cb(device, adv):
        # Optional MAC filter
        if mac_norm and _norm_mac(device.address) != mac_norm:
            return

        svc = adv.service_data or {}

        # If user asked for specific UUID, just report those; otherwise show all service_data blocks
        entries = select_entries(svc, uuid_filter)
        if not entries:
            return

        for uuid_str, payload in entries:
            # E4BE: decode and label fields
            if uuid_str.lower().endswith(UUID_E4BE[-8:]):
                fields = parse_fields_e4be(payload)
                raw32 = fields.get("raw32")
                if raw32 is not None:
                    kg_inst = (tare - raw32) * scale
                    window.append(kg_inst)
                    kg = sum(window) / len(window)
                else:
                    kg = None

                line = f"{datetime.now().isoformat(timespec='seconds')} mac={device.address} rssi={adv.rssi} uuid={uuid_str} "
                line += format_labeled(fields, tare, scale, payload if print_raw else None)
                if kg is not None and smooth_n > 1:
                    line += f" avg_kg={kg:.3f} (n={len(window)})"
                print(line)
            else:
                # Other service data (e.g., FEAA): print hex so we can inspect
                hexstr = binascii.hexlify(payload).decode()
                print(f"{datetime.now().isoformat(timespec='seconds')} mac={device.address} rssi={adv.rssi} uuid={uuid_str} sd={hexstr}")

    return cb

async def main():
    ap = argparse.ArgumentParser(description="RPI BLE scanner that decodes E4BE service data and labels fields.")
    ap.add_argument("--mac", help="Optional target MAC to filter (e.g., 5C:01:3B:35:92:EE)")
    ap.add_argument("--uuid", default=UUID_E4BE, help="Service UUID filter (default E4BE). Use 'all' to show all.")
    ap.add_argument("--tare", type=int, default=DEFAULT_TARE, help="Tare raw32 value (empty reading)")
    ap.add_argument("--scale", type=float, default=DEFAULT_SCALE, help="Scale factor (kg per raw unit)")
    ap.add_argument("--smooth", type=int, default=5, help="Rolling average window for weight_kg display")
    ap.add_argument("--print-raw", action="store_true", help="Print raw service data hex")
    ap.add_argument("--adapter", default="hci0", help="HCI adapter to use (e.g., hci0, hci1)")
    args = ap.parse_args()

    uuid_filter = None if args.uuid.lower() == "all" else args.uuid

    cb = make_callback(args.mac, uuid_filter, args.tare, args.scale, args.smooth, args.print_raw)
    scanner = BleakScanner(detection_callback=cb, adapter=args.adapter, scanning_mode="active")
    await scanner.start()
    print(f"🔍 rpi_ble_scanner.py listening on {args.adapter}... (Ctrl+C to stop)")
    try:
        while True:
            await asyncio.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        await scanner.stop()

if __name__ == "__main__":
    asyncio.run(main())
