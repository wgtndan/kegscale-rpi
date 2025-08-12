
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
adv_paged_multi.py
Collect multiple alternating NO-LOAD / LOAD windows and report frames/indices that
consistently move with load across windows.

Workflow:
  1) Start with NO LOAD on the scale.
  2) Run:
       python3 adv_paged_multi.py --mac XX:... --windows 4 --win-sec 8
     It will prompt you to toggle the load each window.
     Windows alternate: NO, LOAD, NO, LOAD, ...
  3) At the end it prints frames with an index that moves in the same direction
     on both LOAD windows vs both NO-LOAD windows.

Notes:
  - UUID fixed to E4BE; page byte at index 16.
"""
import argparse, asyncio, statistics as stats
from collections import defaultdict
from bleak import BleakScanner

UUID = "0000e4be-0000-1000-8000-00805f9b34fb"
PAGE_INDEX = 16

def u16_pairs_le(b: bytes):
    return [int.from_bytes(b[i:i+2], "little", signed=False) for i in range(max(0, len(b)-1))]

async def collect(mac: str, seconds: float):
    mac_norm = mac.replace(":","").lower()
    acc = defaultdict(lambda: defaultdict(list))  # frame -> idx -> vals

    def cb(device, adv):
        if device.address.replace(":","").lower() != mac_norm:
            return
        sd = (adv.service_data or {}).get(UUID)
        if not sd:
            for k,v in (adv.service_data or {}).items():
                if k.lower().endswith(UUID[-8:].lower()):
                    sd = v; break
        if not sd or len(sd) <= PAGE_INDEX:
            return
        fid = sd[PAGE_INDEX]
        u16 = u16_pairs_le(sd)
        for i,v in enumerate(u16):
            acc[fid][i].append(v)

    async with BleakScanner(detection_callback=cb) as scanner:
        await asyncio.sleep(seconds)

    # summarize means by frame/index
    out = {}
    for fid, m in acc.items():
        out[fid] = {i: (stats.mean(vals), len(vals), stats.pstdev(vals) if len(vals)>1 else 0.0)
                    for i, vals in m.items() if len(vals)>=3}
    return out

def diff_maps(no_map, load_map):
    # returns deltas: fid -> idx -> (delta, base_mean, load_mean, n_base, n_load)
    out = {}
    for fid in set(no_map.keys()) | set(load_map.keys()):
        idxs = set(no_map.get(fid, {}).keys()) | set(load_map.get(fid, {}).keys())
        d = {}
        for i in idxs:
            if i in no_map.get(fid, {}) and i in load_map.get(fid, {}):
                ma, na, _ = no_map[fid][i]
                mb, nb, _ = load_map[fid][i]
                d[i] = (mb-ma, ma, mb, na, nb)
        if d:
            out[fid] = d
    return out

async def main():
    ap = argparse.ArgumentParser(description="Multi-window paged detector (NO/LOAD alternation)")
    ap.add_argument("--mac", required=True)
    ap.add_argument("--windows", type=int, default=4, help="Total windows (even number recommended)")
    ap.add_argument("--win-sec", type=float, default=8.0, help="Seconds per window")
    args = ap.parse_args()

    if args.windows < 2:
        args.windows = 2

    # Alternate NO (even idx) / LOAD (odd idx)
    maps = []
    for w in range(args.windows):
        mode = "NO-LOAD" if w % 2 == 0 else "LOAD"
        input(f"Window {w+1}/{args.windows}: Set to {mode} and press Enter... ")
        print(f"Collecting {args.win_sec:.1f}s of {mode}...")
        m = await collect(args.mac, args.win_sec)
        maps.append((mode, m))

    # Compare first NO vs first LOAD, and second NO vs second LOAD (if present)
    cmp_sets = []
    if len(maps) >= 2:
        cmp_sets.append(diff_maps(maps[0][1], maps[1][1]))
    if len(maps) >= 4:
        cmp_sets.append(diff_maps(maps[2][1], maps[3][1]))

    # Find frames/indices that are present in all comparisons with same delta sign
    consensus = defaultdict(lambda: defaultdict(list))  # fid -> idx -> deltas

    for cm in cmp_sets:
        for fid, dd in cm.items():
            for idx, (d, ma, mb, na, nb) in dd.items():
                consensus[fid][idx].append(d)

    print("\nFrames/indices consistently changing with load across windows:")
    for fid, m in consensus.items():
        rows = []
        for idx, deltas in m.items():
            if len(deltas) == len(cmp_sets):  # present in all comparisons
                same_sign = all((d > 0) == (deltas[0] > 0) for d in deltas)
                if same_sign:
                    # compute average magnitude
                    avg_abs = sum(abs(d) for d in deltas) / len(deltas)
                    rows.append((idx, avg_abs, deltas))
        rows.sort(key=lambda x: x[1], reverse=True)
        if rows:
            print(f"Frame 0x{fid:02x}:")
            for idx, avg_abs, deltas in rows[:8]:
                print(f"  idx {idx:02d}  avg|delta|={avg_abs:.1f}  deltas={', '.join(f'{d:+.0f}' for d in deltas)}")

    print("\nTip: choose the top idx for a frequent frame (use frame_hist.py to see frequency), then calibrate on that frame & idx.")
    print("If you get multiple strong frames with the same idx, include them all with --frame-allow in the main reader.")

if __name__ == "__main__":
    asyncio.run(main())
