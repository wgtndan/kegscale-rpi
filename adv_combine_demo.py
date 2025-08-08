
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
adv_combine_demo.py
Combine fields from multiple frames into one "combined_raw" stream.
Use after you identify per-frame indices with adv_paged_detect.py.

Example:
  python3 adv_combine_demo.py --mac <MAC> --uuid <UUID>     --page-index 16     --map 0x07:8,0x06:12,0x05:4,0x04:10     --op sum --print-raw
"""

import argparse, asyncio, statistics as stats
from typing import Dict, Tuple
from bleak import BleakScanner

def parse_map(s: str) -> Dict[int, int]:
    # format: 0x07:8,0x06:12,0x05:4
    out = {}
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        k, v = part.split(":")
        fid = int(k, 0)
        idx = int(v)
        out[fid] = idx
    return out

def u16_le(sd: bytes, idx: int) -> int:
    if idx+1 >= len(sd): return 0
    return int.from_bytes(sd[idx:idx+2], "little", signed=False)

async def main():
    ap = argparse.ArgumentParser(description="Combine paged fields into a single raw value")
    ap.add_argument("--mac", required=True)
    ap.add_argument("--uuid", default="0000e4be-0000-1000-8000-00805f9b34fb")
    ap.add_argument("--page-index", type=int, default=16)
    ap.add_argument("--map", required=True, help="Mapping frame_id:index pairs, e.g. 0x07:8,0x06:12")
    ap.add_argument("--op", choices=["sum","avg"], default="sum")
    ap.add_argument("--smooth", type=int, default=5)
    ap.add_argument("--print-raw", action="store_true")
    args = ap.parse_args()

    mapping = parse_map(args.map)
    buf = {fid: None for fid in mapping.keys()}
    window = []

    def on_detect(device, adv):
        nonlocal window
        if device.address.replace(":", "").lower() != args.mac.replace(":", "").lower():
            return
        sd = (adv.service_data or {}).get(args.uuid)
        if not sd:
            for k, v in (adv.service_data or {}).items():
                if k.lower().endswith(args.uuid[-8:].lower()):
                    sd = v; break
        if not sd or args.page_index >= len(sd):
            return
        fid = sd[args.page_index]
        idx = mapping.get(fid)
        if idx is None:
            return
        val = u16_le(sd, idx)
        buf[fid] = val

        # combine when at least half are present; fill missing with last known
        present = [v for v in buf.values() if v is not None]
        if len(present) == 0:
            return
        # simple combine
        if args.op == "sum":
            combined = sum(v if v is not None else 0 for v in buf.values())
        else:
            combined = sum(v if v is not None else 0 for v in buf.values()) / max(1, len([v for v in buf.values() if v is not None]))

        window.append(combined)
        if len(window) > max(3, args.smooth):
            window.pop(0)

        med = stats.median(window)
        if args.print_raw:
            print(f"combined_raw={int(med)} parts={buf}")

    print("🔍 Listening (Ctrl+C to stop)")
    try:
        async with BleakScanner(detection_callback=on_detect):
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    asyncio.run(main())
