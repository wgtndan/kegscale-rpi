
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Robust BLE GATT probe with retries and address_type probing (public/random)."""
import argparse
import asyncio
from contextlib import suppress
from bleak import BleakClient

def fmt_val(data: bytes) -> str:
    if not data:
        return "[]"
    hexs = data.hex()
    def u16le(b): return int.from_bytes(b[:2], "little", signed=False) if len(b) >= 2 else None
    def u16be(b): return int.from_bytes(b[:2], "big", signed=False) if len(b) >= 2 else None
    def s16le(b): return int.from_bytes(b[:2], "little", signed=True) if len(b) >= 2 else None
    def u32le(b): return int.from_bytes(b[:4], "little", signed=False) if len(b) >= 4 else None
    parts = [f"hex={hexs}"]
    for name, val in (("u16le", u16le(data)), ("u16be", u16be(data)), ("s16le", s16le(data)), ("u32le", u32le(data))):
        if val is not None: parts.append(f"{name}={val}")
    return " ".join(parts)

async def connect_once(mac: str, address_type: str, timeout: float):
    print(f"🔗 Trying connect to {mac} (type={address_type}, timeout={timeout}s) ...")
    client = BleakClient(mac, timeout=timeout, address_type=address_type)
    await client.__aenter__()
    return client

async def gatt_probe(mac: str, once_read: bool, notify: bool, duration: float, match: str, timeout: float, retries: int):
    last_exc = None
    client = None
    for attempt in range(1, retries+1):
        for addr_type in ("public", "random"):
            try:
                client = await connect_once(mac, addr_type, timeout)
                break
            except Exception as e:
                last_exc = e
                print(f"  ❌ attempt {attempt} type={addr_type}: {e}")
                await asyncio.sleep(1.0)
        if client:
            break
    if not client:
        print(f"❌ Failed to connect after {retries} retries. Last error: {last_exc}")
        return

    try:
        print("✅ Connected")
        svcs = await client.get_services()
        m = (match or "").lower() if match else ""
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
        if notify:
            print(f"\n🔔 Subscribing to notifiable characteristics for {duration:.1f}s...")
            subs = []
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
                            subs.append(ch)
                            print(f"    started notify on {ch.uuid}")
                        except Exception as e:
                            print(f"    notify ! {ch.uuid} -> {e}")
            try:
                await asyncio.sleep(duration)
            finally:
                for ch in subs:
                    with suppress(Exception):
                        await client.stop_notify(ch)
                print("🔕 Stopped notifications.")
    finally:
        with suppress(Exception):
            await client.__aexit__(None, None, None)

def main():
    ap = argparse.ArgumentParser(description="Robust BLE GATT probe")
    ap.add_argument("--mac", required=True, help="MAC address of device")
    ap.add_argument("--read", action="store_true", help="Read all readable characteristics once")
    ap.add_argument("--notify", action="store_true", help="Subscribe to all notifiable characteristics")
    ap.add_argument("--duration", type=float, default=20.0, help="Notify duration in seconds")
    ap.add_argument("--match", type=str, help="Only show UUIDs/descriptions containing this substring")
    ap.add_argument("--timeout", type=float, default=30.0, help="Per-attempt connection timeout seconds")
    ap.add_argument("--retries", type=int, default=3, help="Number of connection retries (tries both address types each time)")
    args = ap.parse_args()
    asyncio.run(gatt_probe(args.mac, args.read, args.notify, args.duration, args.match, args.timeout, args.retries))

if __name__ == "__main__":
    main()
