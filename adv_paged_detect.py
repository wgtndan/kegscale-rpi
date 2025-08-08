
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
adv_paged_detect.py
Detect per-frame (paged) fields that change with load to confirm multi-sensor adverts.

Usage:
  1) Start with NO LOAD on the scale.
  2) Run:
       python3 adv_paged_detect.py --mac <MAC> --no-load-sec 4 --load-sec 4
  3) When prompted, place your known mass and press Enter.
  4) The tool prints, for each observed frame ID (byte at --page-index),
     which u16 pair index changed the most from no-load -> load.

Flags:
  --mac <MAC>              Target device MAC (required)
  --uuid <UUID>            Service UUID key (default E4BE)
  --page-index N           Byte index used as frame/page marker (default 16)
  --no-load-sec S          Seconds to sample with no load (default 4)
  --load-sec S             Seconds to sample with load (default 4)
  --topn K                 Top K indices per frame to report (default 3)
  --min-n N                Minimum samples per (frame,index) to consider (default 3)
  --json                   Also print JSON summaries
"""

import asyncio
import statistics as stats
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional

from bleak import BleakScanner

DEFAULT_SERVICE_UUID = "0000e4be-0000-1000-8000-00805f9b34fb"

@dataclass
class StatWin:
    vals: List[int] = field(default_factory=list)

    def add(self, v: int):
        self.vals.append(v)

    def ok(self, min_n: int) -> bool:
        return len(self.vals) >= min_n

    def mean(self) -> float:
        return float(stats.mean(self.vals)) if self.vals else 0.0

    def sd(self) -> float:
        return float(stats.pstdev(self.vals)) if len(self.vals) > 1 else 0.0

def u16_pairs(sd: bytes) -> List[Tuple[int,int]]:
    out = []
    for i in range(0, max(0, len(sd)-1)):
        le = int.from_bytes(sd[i:i+2], "little", signed=False)
        out.append((i, le))
    return out

async def collect(mac: str, uuid: str, seconds: float, page_index: int) -> Dict[int, Dict[int, StatWin]]:
    """Return nested stats: frame_id -> raw_index -> StatWin"""
    acc: Dict[int, Dict[int, StatWin]] = defaultdict(lambda: defaultdict(StatWin))

    def cb(device, adv):
        if device.address.replace(":", "").lower() != mac.replace(":", "").lower():
            return
        sd = (adv.service_data or {}).get(uuid)
        if not sd:
            for k, v in (adv.service_data or {}).items():
                if k.lower().endswith(uuid[-8:].lower()):
                    sd = v; break
        if not sd:
            return
        if page_index >= len(sd):
            return
        frame_id = sd[page_index]
        for idx, le in u16_pairs(sd):
            acc[frame_id][idx].add(le)

    async with BleakScanner(detection_callback=cb) as scanner:
        await asyncio.sleep(seconds)

    return acc

def summarize_delta(base: Dict[int, Dict[int, StatWin]], load: Dict[int, Dict[int, StatWin]], *, topn: int, min_n: int):
    frames = sorted(set(base.keys()) | set(load.keys()))
    report = []
    for fid in frames:
        idxs = sorted(set(base.get(fid, {}).keys()) | set(load.get(fid, {}).keys()))
        deltas = []
        for idx in idxs:
            b = base.get(fid, {}).get(idx)
            l = load.get(fid, {}).get(idx)
            if not b or not l or not b.ok(min_n) or not l.ok(min_n):
                continue
            ma = b.mean(); mb = l.mean()
            deltas.append((idx, mb - ma, ma, mb, b.sd(), l.sd(), len(b.vals), len(l.vals)))
        deltas.sort(key=lambda x: abs(x[1]), reverse=True)
        report.append((fid, deltas[:topn]))
    # sort frames by max abs delta among their top list
    report.sort(key=lambda item: (abs(item[1][0][1]) if item[1] else 0.0), reverse=True)
    return report

async def main():
    import argparse, json
    ap = argparse.ArgumentParser(description="Detect per-frame changing fields in paged BLE adverts")
    ap.add_argument("--mac", required=True, help="Target device MAC")
    ap.add_argument("--uuid", default=DEFAULT_SERVICE_UUID, help="Service UUID containing service data")
    ap.add_argument("--page-index", type=int, default=16, help="Byte index used as frame/page marker")
    ap.add_argument("--no-load-sec", type=float, default=4.0, help="Seconds to sample with NO LOAD")
    ap.add_argument("--load-sec", type=float, default=4.0, help="Seconds to sample WITH LOAD")
    ap.add_argument("--topn", type=int, default=3, help="Top K indices per frame to report")
    ap.add_argument("--min-n", type=int, default=3, help="Min samples per (frame,index) to consider")
    ap.add_argument("--json", action="store_true", help="Also print JSON summaries")
    args = ap.parse_args()

    print("Step 1/2: Sampling NO-LOAD window...")
    base = await collect(args.mac, args.uuid, args.no_load_sec, args.page_index)
    print("Now add your known load, wait ~1s, then press Enter...")
    input()
    print("Step 2/2: Sampling LOAD window...")
    load = await collect(args.mac, args.uuid, args.load_sec, args.page_index)

    observed_frames = sorted(set(base.keys()) | set(load.keys()))
    print(f"Observed frame IDs (byte@{args.page_index}): {[f'0x{f:02x}' for f in observed_frames]}")

    rep = summarize_delta(base, load, topn=args.topn, min_n=args.min_n)

    print("Top changing u16 pairs per frame (frame_id, index, delta, base->load, base_sd, load_sd, n):")
    for fid, rows in rep:
        print(f"Frame 0x{fid:02x}:")
        if not rows:
            print("  (insufficient samples)")
            continue
        for idx, d, ma, mb, sda, sdb, na, nb in rows:
            print(f"  idx {idx:02d}: delta={d:+.1f}  {ma:.1f}->{mb:.1f}  sd={sda:.1f}/{sdb:.1f}  n={na}/{nb}")

    if args.json:
        # Compact JSON output
        def pack(acc):
            out = {}
            for fid, m in acc.items():
                out[fid] = {idx: {"n": sw.ok(args.min_n) and len(sw.vals) or len(sw.vals),
                                  "mean": sw.mean(),
                                  "sd": sw.sd()} for idx, sw in m.items() if len(sw.vals) > 0}
            return out
        print("-- JSON --")
        print(json.dumps({"base": pack(base), "load": pack(load)}, indent=2))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
