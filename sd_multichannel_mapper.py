
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sd_multichannel_mapper.py
Purpose:
  - Capture E4BE Service Data adverts for a MAC
  - Log raw bytes + u16le windows to CSV
  - Analyze which u16le index per frame_id (page byte) changes most with load
  - Optionally apply a provided mapping (frame_id:index) to emit 4 channels + combined sum

Typical workflow:
  1) NO LOAD capture:
       python3 sd_multichannel_mapper.py --mac XX:XX:... --seconds 8 --out noload.csv
  2) LOAD capture (~1.5 kg on scale):
       python3 sd_multichannel_mapper.py --mac XX:XX:... --seconds 8 --out load.csv
  3) Compare:
       python3 sd_multichannel_mapper.py --compare noload.csv load.csv
     -> prints per-frame "top changing u16 index" suggestions

  Or do a single long capture and let it print per-frame variance ranking at end:
       python3 sd_multichannel_mapper.py --mac XX:XX:... --seconds 30 --summary

  Once you know a mapping, log with channels:
       python3 sd_multichannel_mapper.py --mac XX:... --seconds 20 --map 0x03:12,0x04:12 --emit-channels --out mapped.csv

Notes:
  - Default UUID: 0000e4be-0000-1000-8000-00805f9b34fb
  - page_index default = 16 (last byte in 17B payload)
"""

import argparse, asyncio, csv, os, sys, statistics as stats
from typing import Dict, List, Tuple, Optional
from collections import defaultdict
from bleak import BleakScanner

DEFAULT_UUID = "0000e4be-0000-1000-8000-00805f9b34fb"

def u16_pairs_le(b: bytes) -> List[int]:
    return [int.from_bytes(b[i:i+2], "little", signed=False) for i in range(max(0, len(b)-1))]

def parse_map(s: str) -> Dict[int, int]:
    # "0x03:12,0x04:12" -> {3:12,4:12}
    out = {}
    for part in s.split(","):
        part = part.strip()
        if not part: continue
        k,v = part.split(":")
        out[int(k,0)] = int(v,0)
    return out

def read_csv(path: str) -> List[dict]:
    rows = []
    with open(path, newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            rows.append(row)
    return rows

def summarize_variance(rows: List[dict], exclude_idx={3,5,16}) -> Dict[int, List[Tuple[int, float]]]:
    # Return map: frame_id -> list of (idx, variance) sorted desc
    buckets: Dict[int, Dict[int, List[int]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        try:
            fid = int(row.get("frame_id",""), 10) if row.get("frame_id","").isdigit() else int(row.get("frame_id","") or "0")
        except Exception:
            # try hex like "0x.." else parse as int
            try:
                fid = int(row.get("frame_id"), 0)
            except Exception:
                continue
        # collect u16le[i]
        i = 0
        while True:
            key = f"u16le[{i}]"
            if key not in row: break
            try:
                val = int(float(row[key])) if row[key] != "" else None
            except Exception:
                val = None
            if val is not None and i not in exclude_idx:
                buckets[fid][i].append(val)
            i += 1
    out = {}
    for fid, m in buckets.items():
        scores = []
        for idx, vals in m.items():
            if len(vals) >= 4:
                try:
                    scores.append((idx, stats.pvariance(vals)))
                except Exception:
                    pass
        scores.sort(key=lambda x: x[1], reverse=True)
        out[fid] = scores
    return out

async def capture(mac: str, seconds: int, out_csv: str, uuid: str, page_index: int):
    mac_norm = mac.replace(":","").lower()
    rows = []
    def cb(device, adv):
        if device.address.replace(":","").lower() != mac_norm:
            return
        sd = (adv.service_data or {}).get(uuid)
        if not sd:
            for k,v in (adv.service_data or {}).items():
                if k.lower().endswith(uuid[-8:].lower()):
                    sd = v; break
        if not sd:
            return
        rssi = getattr(adv, "rssi", None)
        b = list(sd)
        u16le = u16_pairs_le(sd)
        row = {
            "timestamp": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
            "mac": device.address,
            "rssi": rssi,
            "len": len(sd),
            "service_data_hex": sd.hex(),
            "frame_id": b[page_index] if page_index < len(b) else None,
        }
        # raw bytes
        for i, bi in enumerate(b):
            row[f"b[{i}]"] = bi
        # u16le windows
        for i, v in enumerate(u16le):
            row[f"u16le[{i}]"] = v
        rows.append(row)

    async with BleakScanner(detection_callback=cb) as scanner:
        await asyncio.sleep(seconds)

    if not rows:
        print("⚠️ No adverts captured. Check MAC/UUID and move device closer.")
        return

    keys = sorted(set().union(*[r.keys() for r in rows]))
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"✅ Wrote {len(rows)} rows to {out_csv}")

def apply_mapping(rows: List[dict], mapping: Dict[int,int], out_csv: str, op: str="sum"):
    # Emit channels per mapping and a combined value
    out_rows = []
    for row in rows:
        try:
            fid = int(row["frame_id"], 0) if str(row["frame_id"]).startswith(("0x","0X")) else int(row["frame_id"])
        except Exception:
            continue
        ch_values = {}
        for fid_m, idx in mapping.items():
            key = f"u16le[{idx}]"
            val = row.get(key)
            ch_values[f"ch_f{fid_m:02x}_i{idx}"] = int(val) if val not in (None,"") else None
        # combine using last-known across frames? For simplicity, single-row combines only those present in this row.
        present = [v for v in ch_values.values() if v is not None]
        if present:
            comb = sum(present) if op == "sum" else sum(present)/len(present)
        else:
            comb = None
        out_row = dict(row)
        out_row.update(ch_values)
        out_row["combined"] = comb
        out_rows.append(out_row)

    keys = sorted(set().union(*[r.keys() for r in out_rows]))
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in out_rows:
            w.writerow(r)
    print(f"✅ Wrote mapped CSV with channels to {out_csv}")

def compare_baseline_load(noload_csv: str, load_csv: str, exclude_idx={3,5,16}):
    a = read_csv(noload_csv)
    b = read_csv(load_csv)
    # compute mean per frame/index for both
    def agg(rows):
        acc = defaultdict(lambda: defaultdict(list))
        for row in rows:
            try:
                fid = int(row["frame_id"], 0) if str(row["frame_id"]).startswith(("0x","0X")) else int(row["frame_id"])
            except Exception:
                continue
            i=0
            while True:
                key=f"u16le[{i}]"
                if key not in row: break
                if i not in exclude_idx and row[key] not in ("", None):
                    try:
                        acc[fid][i].append(int(row[key]))
                    except Exception:
                        pass
                i+=1
        out = {}
        for fid, m in acc.items():
            out[fid] = {idx: (stats.mean(vals), len(vals)) for idx, vals in m.items() if len(vals)>=2}
        return out
    A = agg(a); B = agg(b)
    frames = sorted(set(A.keys()) | set(B.keys()))
    print("Top changing u16 per frame (delta = load_mean - base_mean):")
    for fid in frames:
        pairs = sorted(set(A.get(fid,{}).keys()) | set(B.get(fid,{}).keys()))
        deltas = []
        for idx in pairs:
            ma, na = A.get(fid,{}).get(idx, (0,0))
            mb, nb = B.get(fid,{}).get(idx, (0,0))
            deltas.append((idx, mb-ma, ma, mb, na, nb))
        deltas.sort(key=lambda x: abs(x[1]), reverse=True)
        if deltas[:1]:
            print(f"Frame 0x{fid:02x}:")
            for idx, d, ma, mb, na, nb in deltas[:5]:
                print(f"  idx {idx:02d}: delta={d:+.1f}  {ma:.1f}->{mb:.1f}  n={na}/{nb}")

def main():
    ap = argparse.ArgumentParser(description="E4BE Service Data multichannel mapper/logger")
    sub = ap.add_subparsers(dest="cmd")

    cap = sub.add_parser("capture", help="Capture adverts to CSV")
    cap.add_argument("--mac", required=True)
    cap.add_argument("--seconds", type=int, default=20)
    cap.add_argument("--out", default="sd_capture.csv")
    cap.add_argument("--uuid", default=DEFAULT_UUID)
    cap.add_argument("--page-index", type=int, default=16)

    comp = sub.add_parser("compare", help="Compare two CSVs (no-load vs load)")
    comp.add_argument("noload_csv")
    comp.add_argument("load_csv")

    summ = sub.add_parser("summary", help="Print per-frame variance ranking from a CSV")
    summ.add_argument("csvfile")

    amap = sub.add_parser("map", help="Apply a frame:index mapping and emit channels + combined")
    amap.add_argument("csvfile")
    amap.add_argument("--map", required=True, help="e.g., 0x03:12,0x04:12")
    amap.add_argument("--out", default="sd_mapped.csv")
    amap.add_argument("--op", choices=["sum","avg"], default="sum")

    args = ap.parse_args()

    if args.cmd == "capture":
        asyncio.run(capture(args.mac, args.seconds, args.out, args.uuid, args.page_index))
    elif args.cmd == "compare":
        compare_baseline_load(args.noload_csv, args.load_csv)
    elif args.cmd == "summary":
        rows = read_csv(args.csvfile)
        varmap = summarize_variance(rows)
        for fid, scores in varmap.items():
            print(f"Frame 0x{fid:02x}:")
            for idx, var in scores[:8]:
                print(f"  idx {idx:02d} var={var:.1f}")
    elif args.cmd == "map":
        rows = read_csv(args.csvfile)
        mapping = parse_map(args.map)
        apply_mapping(rows, mapping, args.out, op=args.op)
    else:
        ap.print_help()

if __name__ == "__main__":
    main()
