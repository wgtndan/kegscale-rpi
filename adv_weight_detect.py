
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio, statistics as stats, time
from collections import defaultdict
from bleak import BleakScanner

def u16_pairs(sd: bytes):
    out = []
    for i in range(0, max(0, len(sd)-1)):
        le = int.from_bytes(sd[i:i+2], "little", signed=False)
        out.append((i, le))
    return out

async def collect(mac, uuid, seconds, label):
    acc = defaultdict(list)
    def cb(device, adv):
        if device.address.replace(":", "").lower() != mac.replace(":", "").lower():
            return
        sd = (adv.service_data or {}).get(uuid)
        if not sd:
            for k,v in (adv.service_data or {}).items():
                if k.lower().endswith(uuid[-8:].lower()):
                    sd = v; break
        if not sd:
            return
        for i, le in u16_pairs(sd):
            acc[i].append(le)
    async with BleakScanner(detection_callback=cb) as scanner:
        await asyncio.sleep(seconds)
    summary = {}
    for i, vals in acc.items():
        if len(vals) < 3: 
            continue
        summary[i] = {
            "n": len(vals),
            "mean": stats.mean(vals),
            "sd": stats.pstdev(vals),
            "min": min(vals),
            "max": max(vals),
        }
    return summary

def top_deltas(a, b, topn=6):
    keys = sorted(set(a.keys()) | set(b.keys()))
    rows = []
    for i in keys:
        ma = a.get(i, {"mean": 0})["mean"]
        mb = b.get(i, {"mean": 0})["mean"]
        rows.append((i, mb - ma, ma, mb))
    rows.sort(key=lambda x: abs(x[1]), reverse=True)
    return rows[:topn]

async def main():
    import argparse, json
    ap = argparse.ArgumentParser(description="Detect likely weight field from adverts by diffing two windows")
    ap.add_argument("--mac", required=True, help="Target MAC")
    ap.add_argument("--uuid", default="0000e4be-0000-1000-8000-00805f9b34fb", help="Service UUID containing service data")
    ap.add_argument("--no-load-sec", type=float, default=4.0, help="Seconds to sample with NO LOAD")
    ap.add_argument("--load-sec", type=float, default=4.0, help="Seconds to sample WITH LOAD")
    ap.add_argument("--json", action="store_true", help="Print JSON summaries as well")
    args = ap.parse_args()

    print("Step 1/2: Sampling NO-LOAD window...")
    base = await collect(args.mac, args.uuid, args.no_load_sec, "no_load")
    print("Now add your known load, wait ~1s, then press Enter...")
    input()
    print("Step 2/2: Sampling LOAD window...")
    load = await collect(args.mac, args.uuid, args.load_sec, "load")

    print("\nTop changing u16 pairs (index, delta=load- baseline, baseline_mean -> load_mean):")
    for i, d, ma, mb in top_deltas(base, load):
        print(f"  idx {i:02d}: delta={d:+.1f}  {ma:.1f} -> {mb:.1f}")

    if args.json:
        print("\n-- JSON summaries --")
        import json
        print(json.dumps({"no_load": base, "load": load}, indent=2))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
