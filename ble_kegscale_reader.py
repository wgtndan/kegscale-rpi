
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BLE Keg Scale Reader & Calibrator (E4BE)
- Advert mode only (your device is non-connectable): parse fields from Service Data
- Configurable indices: --raw-index, --batt-index, --temp-index
- NEW: --raw-shift to drop flaggy low bits (e.g., --raw-shift 8 to keep only the high byte)
- Median-on-raw smoothing + stability gate
- Two-point calibration persisted in JSON
- Debug helpers: --hex-dump, --scan-fields, --dump-u16
"""

import argparse
import asyncio as _asyncio
from contextlib import suppress
import json
import statistics as stats
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from bleak import BleakScanner

DEFAULT_SERVICE_UUID = "0000e4be-0000-1000-8000-00805f9b34fb"


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def clip(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def scan_u16_pairs(sd: bytes) -> List[Tuple[int,int,int]]:
    out = []
    for i in range(0, max(0, len(sd)-1)):
        le = int.from_bytes(sd[i:i+2], "little", signed=False)
        be = int.from_bytes(sd[i:i+2], "big", signed=False)
        out.append((i, le, be))
    return out


def parse_service_data(sd: bytes, *, args, dump_u16: bool = False) -> Dict[str, Any]:
    if sd is None:
        raise ValueError("service_data is None")
    if len(sd) < 2:
        raise ValueError(f"service_data too short: {len(sd)} bytes")

    # Battery (single byte)
    batt_raw = sd[args.batt_index] if args.batt_index < len(sd) else 0

    # Temperature (two bytes, deci-degC, LE by observation)
    if args.temp_index + 1 < len(sd):
        temp_raw_le = int.from_bytes(sd[args.temp_index:args.temp_index+2], byteorder="little", signed=args.temp_signed)
        temp_c = temp_raw_le / 10.0
    else:
        temp_raw_le = 0
        temp_c = 0.0

    # RAW u16 window
    if args.raw_index + 1 >= len(sd):
        raise ValueError(f"raw_index {args.raw_index} out of range for len={len(sd)}")
    raw_u16 = int.from_bytes(sd[args.raw_index:args.raw_index+2], byteorder=args.endian, signed=False)

    out = {
        "batt_raw": batt_raw,
        "temp_raw": temp_raw_le,
        "temp_c": temp_c,
        "raw_u16": raw_u16,
        "b_raw0": sd[args.raw_index],
        "b_raw1": sd[args.raw_index+1],
        "len": len(sd),
    }

    if dump_u16:
        out["raw_le"] = int.from_bytes(sd[args.raw_index:args.raw_index+2], byteorder="little", signed=False)
        out["raw_be"] = int.from_bytes(sd[args.raw_index:args.raw_index+2], byteorder="big", signed=False)

    return out


def map_temp(temp_c: float, temp_map: Optional[Dict[str, float]]) -> float:
    if not temp_map:
        return temp_c
    a = temp_map.get("a", 1.0)
    b = temp_map.get("b", 0.0)
    return a * temp_c + b


def map_batt(batt_raw: int, batt_map: Optional[Dict[str, float]]) -> int:
    if not batt_map:
        # If it already looks like percent 0..100, accept it
        if 0 <= batt_raw <= 100:
            return batt_raw
        # Else fallback - scale 0..15 -> 0..100%
        if batt_raw <= 0x0F:
            pct = int(round((batt_raw / 15.0) * 100.0))
        else:
            pct = int(round((batt_raw / 255.0) * 100.0))
        return clip(pct, 0, 100)
    a = batt_map.get("a", 6.67)
    b = batt_map.get("b", 0.0)
    pct = int(round(a * batt_raw + b))
    return clip(pct, 0, 100)


def stable(buf: deque, s_thresh: float, slope_thresh: float) -> bool:
    if len(buf) < buf.maxlen:
        return False
    sd = stats.pstdev(buf)
    slope = (buf[-1] - buf[0]) / max(1, len(buf) - 1)
    return sd < s_thresh and abs(slope) < slope_thresh


async def run_scanner(callback, scan_time: float = None):
    async with BleakScanner(detection_callback=callback) as scanner:
        if scan_time is None:
            while True:
                await _asyncio.sleep(3600)
        else:
            await _asyncio.sleep(scan_time)
    return


async def collect_mean_raw(target_mac: Optional[str], target_uuid: str, *, seconds: float, args,
                           debug: bool = False) -> int:
    target_mac_norm = target_mac.replace(":", "").lower() if target_mac else None
    raws = []

    def on_detect(device, adv):
        nonlocal raws
        mac_norm = device.address.replace(":", "").lower()
        if target_mac_norm and mac_norm != target_mac_norm:
            return
        sd_dict = adv.service_data or {}
        sd = sd_dict.get(target_uuid)
        if not sd:
            for k, v in sd_dict.items():
                if k.lower().endswith(target_uuid[-8:].lower()):
                    sd = v
                    break
        if not sd:
            return
        try:
            fields = parse_service_data(sd, args=args, dump_u16=False)
            raw_val = fields["raw_u16"] >> max(0, args.raw_shift)
            raws.append(raw_val)
            if debug:
                print(f"[collect] len={len(sd)} raw_u16={fields['raw_u16']} >>{args.raw_shift} -> {raw_val} "
                      f"@[{args.raw_index}:{args.raw_index+2}] b0=0x{fields['b_raw0']:02x} b1=0x{fields['b_raw1']:02x}")
        except Exception as e:
            if debug:
                print(f"[collect] parse error: {e}")

    task = _asyncio.create_task(run_scanner(on_detect, scan_time=seconds))
    with suppress(Exception):
        await task
    if not raws:
        raise RuntimeError("No samples captured during calibration window. Check MAC/UUID/indices.")
    return int(round(sum(raws) / len(raws)))


def load_cal(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_cal(path: str, cal: Dict[str, Any]):
    p = Path(path)
    p.write_text(json.dumps(cal, indent=2, sort_keys=True), encoding="utf-8")


async def live_read(args):
    cal = load_cal(args.load_cal or args.save_cal)
    slope = cal.get("slope")
    intercept = cal.get("intercept")
    temp_map = cal.get("temp_map")
    batt_map = cal.get("batt_map")

    displayed_kg = 0.0
    zero_offset = 0.0 if not args.zero else 0.0
    raw_buf = deque(maxlen=max(3, args.smooth))

    last_dump = 0

    def on_detect(device, adv):
        nonlocal displayed_kg, zero_offset, last_dump
        if args.mac:
            if device.address.replace(":", "").lower() != args.mac.replace(":", "").lower():
                return
        sd_dict = adv.service_data or {}
        sd = sd_dict.get(args.uuid)
        if not sd:
            for k, v in sd_dict.items():
                if k.lower().endswith(args.uuid[-8:].lower()):
                    sd = v
                    break
        if not sd:
            return

        try:
            if args.hex_dump or args.scan_fields:
                import time
                t = int(time.time())
                if t != last_dump:
                    last_dump = t
                    print(f"[hex] len={len(sd)} {sd.hex()}")

            if args.scan_fields:
                pairs = scan_u16_pairs(sd)
                line = " ".join([f"[{i:02d}]le={le:5d}/be={be:5d}" for i, le, be in pairs])
                print(f"[u16] {line}")

            fields = parse_service_data(sd, args=args, dump_u16=args.dump_u16)

            temp_c_mapped = map_temp(fields["temp_c"], temp_map)
            batt_pct = map_batt(fields["batt_raw"], batt_map)

            raw_val = fields["raw_u16"] >> max(0, args.raw_shift)
            raw_buf.append(raw_val)
            raw_med = stats.median(raw_buf)

            if slope is not None and intercept is not None:
                kg_now = slope * raw_med + intercept
            else:
                kg_now = 0.0  # uncalibrated

            if args.zero:
                if zero_offset == 0.0:
                    zero_offset = kg_now
                kg_zeroed = max(0.0, kg_now - zero_offset)
            else:
                kg_zeroed = kg_now

            commit = stable(raw_buf, s_thresh=args.stable_sd, slope_thresh=args.stable_slope)
            if commit:
                displayed_kg = kg_zeroed

            if args.print_raw:
                rssi = getattr(adv, "rssi", None)
                tstamp = now_iso()
                print(
                    f"{tstamp}  kg={displayed_kg:.3f} zeroed={(kg_zeroed if args.zero else 0):.3f} "
                    f"raw={int(raw_med)}  temp={temp_c_mapped:.1f}°C  batt={batt_pct}%"
                )

        except Exception as e:
            if args.debug:
                print(f"[read] parse error: {e}")

    print("🔍 Listening for BLE advertisements... (Ctrl+C to stop)")
    try:
        await run_scanner(on_detect, scan_time=None)
    except KeyboardInterrupt:
        pass


async def calibrate(args):
    print("🧪 Calibration mode")
    print("1) Leave the scale EMPTY and press Enter. I will average raw for a few seconds...")
    input()
    R0 = await collect_mean_raw(args.mac, args.uuid, seconds=args.cal_seconds, args=args, debug=args.debug)
    print(f"   -> Empty mean raw = {R0}")

    print(f"2) Place known mass ({args.calibrate:.3f} kg), wait for it to settle, then press Enter.")
    input()
    R1 = await collect_mean_raw(args.mac, args.uuid, seconds=args.cal_seconds, args=args, debug=args.debug)
    print(f"   -> Loaded mean raw = {R1}")

    if R1 == R0:
        raise RuntimeError("Calibration failed: R1 equals R0; no delta.")

    slope = float(args.calibrate) / float(R1 - R0)
    intercept = -slope * R0

    cal = load_cal(args.load_cal or args.save_cal)
    cal.update({
        "slope": slope,
        "intercept": intercept,
        "parser": {
            "endian": args.endian,
            "raw_bytes": [args.raw_index, args.raw_index+1],
            "raw_shift": args.raw_shift,
            "uuid": args.uuid,
        },
        "batt_map": cal.get("batt_map"),
        "temp_map": cal.get("temp_map"),
        "updated": now_iso(),
    })

    if args.save_cal:
        save_cal(args.save_cal, cal)
        print(f"✅ Saved calibration to {args.save_cal}")
        print(json.dumps({k: cal[k] for k in ("slope","intercept","parser","updated")}, indent=2))

    return cal


def build_arg_parser():
    p = argparse.ArgumentParser(description="BLE Keg Scale Reader & Calibrator (E4BE)")
    p.add_argument("--mac", help="Filter by MAC address (colon-separated or not)", default=None)
    p.add_argument("--uuid", default=DEFAULT_SERVICE_UUID, help="Service UUID key containing the service data")
    p.add_argument("--endian", choices=["little", "big"], default="little", help="Endian for RAW field")

    p.add_argument("--raw-index", type=int, default=15, help="Start byte index for RAW u16 field")
    p.add_argument("--raw-shift", type=int, default=0, help="Right shift to apply to RAW before use (e.g., 8)")
    p.add_argument("--batt-index", type=int, default=3, help="Byte index for battery raw")
    p.add_argument("--temp-index", type=int, default=5, help="Start byte for temperature (deci-degC, LE)")
    p.add_argument("--temp-signed", action="store_true", default=True, help="Treat temperature as signed 16-bit")
    p.add_argument("--temp-unsigned", dest="temp_signed", action="store_false", help="Treat temperature as unsigned")

    p.add_argument("--smooth", type=int, default=5, help="Median smoothing window (raw counts)")
    p.add_argument("--zero", action="store_true", help="Start in zeroed (tare) mode")

    p.add_argument("--print-raw", action="store_true", help="Print live rows of parsed output")
    p.add_argument("--debug", action="store_true", help="Verbose debug including parse info")
    p.add_argument("--dump-u16", action="store_true", help="Add raw_le/raw_be to parse (debug only)")
    p.add_argument("--hex-dump", action="store_true", help="Print a hex dump of service data periodically")
    p.add_argument("--scan-fields", action="store_true", help="Scan and print all u16 pairs to find the right offsets")

    p.add_argument("--calibrate", type=float, default=None, help="Enter calibration mode with known mass (kg)")
    p.add_argument("--cal-seconds", type=float, default=4.0, help="Seconds to average raw during each cal step")
    p.add_argument("--save-cal", default="keg_cal.json", help="Path to save calibration JSON")
    p.add_argument("--load-cal", default=None, help="Path to load calibration JSON (else will use --save-cal if exists)")

    p.add_argument("--temp-comp", action="store_true", help="Reserved (no-op in advert mode)")
    p.add_argument("--stable-sd", type=float, default=4.0, help="Stability gate: stdev threshold on raw counts")
    p.add_argument("--stable-slope", type=float, default=1.5, help="Stability gate: absolute slope counts/step threshold")

    return p


async def main():
    args = build_arg_parser().parse_args()

    if args.calibrate is not None:
        await calibrate(args)
        return

    await live_read(args)


if __name__ == "__main__":
    try:
        _asyncio.run(main())
    except KeyboardInterrupt:
        pass
