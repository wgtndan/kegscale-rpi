#!/usr/bin/env python3
from __future__ import annotations
import asyncio
import binascii
import uuid
from datetime import datetime, timezone
from bleak import BleakScanner

E4BE_UUID = uuid.UUID("0000e4be-0000-1000-8000-00805f9b34fb")
EDDYSTONE_UUID = uuid.UUID("0000feaa-0000-1000-8000-00805f9b34fb")

def _now_iso():
    return datetime.now(timezone.utc).isoformat()

def _fmt_service_data(sd: dict) -> str:
    parts = []
    for k, v in (sd or {}).items():
        try:
            u = str(uuid.UUID(str(k)))
        except Exception:
            u = str(k)
        parts.append(f"{u}={binascii.hexlify(v).decode()}")
    return "{" + ", ".join(parts) + "}"

def _fmt_mfg(mfg: dict) -> str:
    return "{" + ", ".join(f"{cid:04x}:{binascii.hexlify(v).decode()}" for cid, v in (mfg or {}).items()) + "}"

def cb(device, ad):
    line1 = (
        f"{_now_iso()} mac={device.address} rssi={ad.rssi} "
        f"name={ad.local_name or ''} uuids={list(ad.service_uuids or [])}"
    )
    line2 = f"    mfg={_fmt_mfg(ad.manufacturer_data)}"
    line3 = f"    svc={_fmt_service_data(ad.service_data)}"

    print(line1)
    print(line2)
    print(line3)

async def main():
    scanner = BleakScanner(callback=cb, scanning_mode="active")  # active scan is noisier but more reliable
    await scanner.start()
    print("🔍 Verbose scan running... (Ctrl+C to stop)")
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        await scanner.stop()

if __name__ == "__main__":
    asyncio.run(main())
