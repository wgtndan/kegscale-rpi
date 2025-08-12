
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fe_weight_reader.py
Focuses on E4BE adverts, frame 0xFE @ byte[16], weight at u16 LE starting byte index 12.

Features:
- --calibrate <kg> two-step calibration (no-load then known mass)
- Saves slope/intercept to JSON
- Live read with median smoothing & optional zeroing (tare)
- Also prints battery (b[3]) and temperature from b[5:7] (LE deci-°C)

Usage examples:
  # Calibrate with 1.53 kg
  python3 fe_weight_reader.py --mac XX:XX:... --calibrate 1.53 --save-cal keg_cal.json

  # Live read
  python3 fe_weight_reader.py --mac XX:XX:... --load-cal keg_cal.json --smooth 5 --print-raw
"""

import argparse, asyncio, json, statistics as stats
from collections import deque
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from bleak import BleakScanner

UUID = "0000e4be-0000-1000-8000-00805f9b34fb"
FRAME_INDEX = 16
FRAME_FE = 0xFE
RAW_INDEX = 12  # u16 LE at bytes 12..13

def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

def load_cal(path):
    p = Path(path) if path else None
    if not p or not p.exists():
        return {}
    return json.loads(p.read_text())

def save_cal(path, cal):
    Path(path).write_text(json.dumps(cal, indent=2, sort_keys=True))

def parse_sd(sd: bytes):
    if len(sd) < 17:
        return None
    frame_id = sd[FRAME_INDEX]
    if frame_id != FRAME_FE:
        return None
    raw_u16 = int.from_bytes(sd[RAW_INDEX:RAW_INDEX+2], "little", signed=False)
    batt = sd[3] if len(sd) > 3 else None
    temp_raw = int.from_bytes(sd[5:7], "little", signed=False) if len(sd) >= 7 else None
    temp_c = (temp_raw / 10.0) if temp_raw is not None else None
    return {"frame": frame_id, "raw": raw_u16, "batt": batt, "temp_c": temp_c}

async def scan_collect_avg(mac: str, seconds: float):
    mac_norm = mac.replace(":","").lower()
    vals = []
    def cb(device, adv):
        if device.address.replace(":","").lower() != mac_norm:
            return
        sd = (adv.service_data or {}).get(UUID)
        if not sd:
            for k,v in (adv.service_data or {}).items():
                if k.lower().endswith(UUID[-8:].lower()):
                    sd = v; break
        if not sd:
            return
        rec = parse_sd(sd)
        if rec:
            vals.append(rec["raw"])
    async with BleakScanner(detection_callback=cb) as scanner:
        await asyncio.sleep(seconds)
    if not vals:
        raise RuntimeError("No samples captured for frame 0xFE during window.")
    return sum(vals)/len(vals)

async def calibrate(mac: str, kg_known: float, seconds: float, save_path: str):
    print("🧪 Calibration mode for frame 0xFE @ idx 12 (u16 LE)")
    input("1) Leave the scale EMPTY, then press Enter... ")
    r0 = await scan_collect_avg(mac, seconds)
    print(f"   -> Empty mean raw = {r0:.2f}")
    input(f"2) Place known mass ({kg_known:.3f} kg), let it settle, then press Enter... ")
    r1 = await scan_collect_avg(mac, seconds)
    print(f"   -> Loaded mean raw = {r1:.2f}")
    if r1 == r0:
        raise RuntimeError("Calibration failed (no delta).")
    slope = kg_known / (r1 - r0)
    intercept = -slope * r0
    cal = {"slope": float(slope), "intercept": float(intercept), "parser": {
        "uuid": UUID, "frame": FRAME_FE, "raw_index": RAW_INDEX, "endian": "little"
    }}
    if save_path:
        save_cal(save_path, cal)
        print(f"✅ Saved calibration to {save_path}")
        print(json.dumps(cal, indent=2))
    return cal

async def live(mac: str, cal_path: str, smooth: int, zero: bool, print_raw: bool):
    cal = load_cal(cal_path) if cal_path else {}
    slope = cal.get("slope"); intercept = cal.get("intercept")
    if slope is None or intercept is None:
        print("⚠️ No calibration loaded; kg will read as 0. Use --calibrate first.")
    mac_norm = mac.replace(":","").lower()
    buf = deque(maxlen=max(3, smooth))
    zero_offset = None
    displayed_kg = 0.0

    def cb(device, adv):
        nonlocal zero_offset, displayed_kg
        if device.address.replace(":","").lower() != mac_norm:
            return
        sd = (adv.service_data or {}).get(UUID)
        if not sd:
            for k,v in (adv.service_data or {}).items():
                if k.lower().endswith(UUID[-8:].lower()):
                    sd = v; break
        if not sd:
            return
        rec = parse_sd(sd)
        if not rec:
            return
        buf.append(rec["raw"])
        if len(buf) == buf.maxlen:
            raw_med = stats.median(buf)
            if slope is not None and intercept is not None:
                kg_now = slope * raw_med + intercept
            else:
                kg_now = 0.0
            if zero:
                if zero_offset is None:
                    zero_offset = kg_now
                kg_zeroed = max(0.0, kg_now - zero_offset)
            else:
                kg_zeroed = kg_now
            displayed_kg = kg_zeroed
            if print_raw:
                print(f"{now_iso()}  kg={displayed_kg:.3f} raw={int(raw_med)} temp={rec['temp_c']:.1f}°C batt={rec['batt']}% frame=0x{rec['frame']:02x}")

    print("🔍 Listening for frame 0xFE only... (Ctrl+C to stop)")
    try:
        async with BleakScanner(detection_callback=cb):
            while True:
                await asyncio.sleep(3600)
    except KeyboardInterrupt:
        pass

def main():
    ap = argparse.ArgumentParser(description="Frame-0xFE weight reader for E4BE adverts (idx 12 u16 LE)")
    ap.add_argument("--mac", required=True, help="Device MAC")
    ap.add_argument("--save-cal", default="keg_cal.json", help="Where to save calibration JSON")
    ap.add_argument("--load-cal", default="keg_cal.json", help="Where to load calibration JSON from")
    ap.add_argument("--calibrate", type=float, default=None, help="Known mass (kg) to calibrate")
    ap.add_argument("--cal-seconds", type=float, default=5.0, help="Seconds to average during each cal step")
    ap.add_argument("--smooth", type=int, default=5, help="Median smoothing window")
    ap.add_argument("--zero", action="store_true", help="Tare (zero) at start of live read")
    ap.add_argument("--print-raw", action="store_true", help="Print live rows")
    args = ap.parse_args()

    if args.calibrate is not None:
        asyncio.run(calibrate(args.mac, args.calibrate, args.cal_seconds, args.save_cal))
    else:
        asyncio.run(live(args.mac, args.load_cal, args.smooth, args.zero, args.print_raw))

if __name__ == "__main__":
    main()
