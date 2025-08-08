#!/usr/bin/env python3
"""
BLE GATT Probe Tool
- Scans all services/characteristics for a given MAC
- Optionally reads once (--read)
- Optionally subscribes to notifications (--notify)
"""

import argparse
import asyncio
from contextlib import suppress
from bleak import BleakClient

def fmt_val(data: bytes) -> str:
    """Format characteristic value in hex and numeric forms."""
    if not data:
        return "[]"
    hexs = data.hex()
    def to_u16_le(b): return int.from_bytes(b[:2], "little", signed=False) if len(b) >= 2 else None
    def to_u16_be(b): return int.from_bytes(b[:2], "big", signed=False) if len(b) >= 2 else None
    def to_s16_le(b): return int.from_bytes(b[:2], "little", signed=True) if len(b) >= 2 else None
    def to_u32_le(b): return int.from_bytes(b[:4], "little", signed=False) if len(b) >= 4 else None
    u16le = to_u16_le(data); u16be = to_u16_be(data); s16le = to_s16_le(data); u32le = to_u32_le(data)
    parts = [f"hex={hexs}"]
    if u16le is not None: parts.append(f"u16le={u16le}")
    if u16be is not None: parts.append(f"u16be={u16be}")
    if s16le is not None: parts.append(f"s16le={s16le}")
    if u32le is not None: parts.append(f"u32le={u32le}")
    return " ".join(parts)

async def gatt_probe(mac: str, once_read: bool, notify: bool, duration: float, match: str):
    print(f"🔗 Connecting to {mac} ...")
    async with BleakClient(mac, timeout=15.0) as client:
        print("✅ Connected")
        svcs = await client.get_services()
        m = (match or "").lower()
        for svc in svcs:
            if m and (m not in str(svc.uuid).lower() and m not in (svc.description or "").lower()):
                continue
            print(f"\nService {svc.uuid}  ({svc.description})")
            for ch in svc.characteristics:
                if m and (m not in str(ch.uuid).lower() and m not in (ch.description or "").lower()):
                    continue
                props = ",".join(ch.properties)
                print(f"  Char {ch.uuid}  props=[{props}]  desc={ch.description}")
                if once_read and "read" in ch.properties:
                    try:
                        data = await client.read_gatt_char(ch)
                        print(f"    READ -> {fmt_val(bytes(data))}")
                    except Exception as e:
                        print(f"    READ ! {e}")
        if not notify:
            return
        print(f"\n🔔 Subscribing to notifiable characteristics for {duration:.1f}s...")
        unsub = []
        def make_cb(ch_uuid):
            def cb(sender, data: bytearray):
                print(f"    NOTIFY {ch_uuid} -> {fmt_val(bytes(data))}")
            return cb
        for svc in svcs:
            if m and (m not in str(svc.uuid).lower() and m not in (svc.description or "").lower()):
                continue
            for ch in svc.characteristics:
                if m and (m not in str(ch.uuid).lower() and m not in (ch.description or "").lower()):
                    continue
                if "notify" in ch.properties:
                    try:
                        cb = make_cb(ch.uuid)
                        await client.start_notify(ch, cb)
                        unsub.append(ch)
                        print(f"    started notify on {ch.uuid}")
                    except Exception as e:
                        print(f"    notify ! {ch.uuid} -> {e}")
        try:
            await asyncio.sleep(duration)
        finally:
            for ch in unsub:
                with suppress(Exception):
                    await client.stop_notify(ch)
            print("🔕 Stopped notifications.")

def main():
    ap = argparse.ArgumentParser(description="BLE GATT probe tool")
    ap.add_argument("--mac", required=True, help="MAC address of device")
    ap.add_argument("--read", action="store_true", help="Read all readable characteristics once")
    ap.add_argument("--notify", action="store_true", help="Subscribe to all notifiable characteristics")
    ap.add_argument("--duration", type=float, default=20.0, help="Notify duration in seconds")
    ap.add_argument("--match", type=str, help="Only show UUIDs/descriptions containing this substring")
    args = ap.parse_args()
    asyncio.run(gatt_probe(args.mac, args.read, args.notify, args.duration, args.match))

if __name__ == "__main__":
    main()
