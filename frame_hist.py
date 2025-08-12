
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
frame_hist.py
Scan E4BE adverts and show a histogram of frame IDs (byte at index 16).

Usage:
  python3 frame_hist.py --mac XX:... --seconds 30
"""
import argparse, asyncio, collections
from bleak import BleakScanner

UUID = "0000e4be-0000-1000-8000-00805f9b34fb"

async def main():
    ap = argparse.ArgumentParser(description="List frame ID frequencies at b[16]")
    ap.add_argument("--mac", required=True)
    ap.add_argument("--seconds", type=int, default=30)
    args = ap.parse_args()

    mac_norm = args.mac.replace(":","").lower()
    hist = collections.Counter()

    def cb(device, adv):
        if device.address.replace(":","").lower() != mac_norm:
            return
        sd = (adv.service_data or {}).get(UUID)
        if not sd:
            for k,v in (adv.service_data or {}).items():
                if k.lower().endswith(UUID[-8:].lower()):
                    sd = v; break
        if not sd or len(sd) <= 16:
            return
        fid = sd[16]
        hist[fid] += 1

    async with BleakScanner(detection_callback=cb) as scanner:
        await asyncio.sleep(args.seconds)

    total = sum(hist.values())
    if not total:
        print("No frames seen; check MAC and proximity.")
        return

    print(f"Observed {total} adverts. Top frames:")
    for fid, cnt in hist.most_common(16):
        pct = 100.0*cnt/total
        print(f"  frame 0x{fid:02x}: {cnt} ({pct:.1f}%)")

if __name__ == "__main__":
    asyncio.run(main())
