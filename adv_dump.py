
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio
from bleak import BleakScanner

async def main(mac_filter=None, seconds=12):
    def cb(device, adv):
        if mac_filter and device.address.lower().replace(":", "") != mac_filter.lower().replace(":", ""):
            return
        md = adv.manufacturer_data or {}
        sd = adv.service_data or {}
        mdf = " ".join([f"{k:04x}:{v.hex()}" for k, v in md.items()])
        sdf = " ".join([f"{k}:{v.hex()}" for k, v in sd.items()])
        print(f"{device.address} name={device.name} rssi={adv.rssi} connectable={getattr(adv,'connectable',None)}")
        if adv.tx_power is not None:
            print(f"  tx_power={adv.tx_power}")
        if mdf:
            print(f"  mfg: {mdf}")
        if sdf:
            print(f"  svc: {sdf}")
        print("-")
    async with BleakScanner(detection_callback=cb) as scanner:
        await asyncio.sleep(seconds)

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Dump BLE advertisements (service & manufacturer data)")
    ap.add_argument("--mac", help="Filter to this MAC (optional)")
    ap.add_argument("--seconds", type=int, default=12, help="How long to listen")
    args = ap.parse_args()
    asyncio.run(main(args.mac, args.seconds))
