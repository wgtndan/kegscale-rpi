
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BLE Keg Scale Reader & Calibrator
- Filters BLE adverts for a device broadcasting Service Data under UUID E4BE
- Parses raw reading from bytes 12-13 (configurable endian), temp & batt from service data
- Median smoothing on RAW, stability gate before committing displayed kg
- Two-point calibration + optional temp compensation
- Persisted config & calibration in JSON

Tested with Python 3.11 and bleak 1.x on Raspberry Pi.
"""

import argparse
import asyncio as _asyncio
from contextlib import suppress
import json
import statistics as stats
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

from bleak import BleakScanner

DEFAULT_SERVICE_UUID = "0000e4be-0000-1000-8000-00805f9b34fb"


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def clip(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def parse_service_data(sd: bytes, *, endian: str, dump_u16: bool = False) -> Dict[str, Any]:
    """Parse fields we currently understand from the service data payload.
    Layout (best current guess):
      byte 2   -> batt_raw (0-255) (often ~0x0F observed)
      bytes 5-6 -> temperature in deci-°C (little-endian, signed) -> temp_c = /10
      bytes 12-13 -> RAW reading (configurable endian)
    """
    if sd is None or len(sd) < 14:
        raise ValueError(f"service_data too short: {None if sd is None else len(sd)} bytes" )

    batt_raw = sd[2]

    # temperature: signed little-endian deci-degC (observed)
    temp_raw_le = int.from_bytes(sd[5:7], byteorder="little", signed=True)
    temp_c = temp_raw_le / 10.0

    if endian not in ("little", "big"):
        raise ValueError("endian must be 'little' or 'big'")
    raw = int.from_bytes(sd[12:14], byteorder=endian, signed=False)

    out = {
        "batt_raw": batt_raw,
        "temp_raw": temp_raw_le,
        "temp_c": temp_c,
        "raw": raw,
        "b12": sd[12],
        "b13": sd[13],
    }

    if dump_u16:
        out["raw_le"] = int.from_bytes(sd[12:14], byteorder="little", signed=False)
        out["raw_be"] = int.from_bytes(sd[12:14], byteorder="big", signed=False)

    return out


def map_temp(temp_c: float, temp_map: Optional[Dict[str, float]]) -> float:
    if not temp_map:
        return temp_c
    a = temp_map.get("a", 1.0)
    b = temp_map.get("b", 0.0)
    return a * temp_c + b


def map_batt(batt_raw: int, batt_map: Optional[Dict[str, float]]) -> int:
    if not batt_map:
        # Fallback heuristic - scale 0..15 -> 0..100%
        if batt_raw <= 0x0F:
            pct = int(round((batt_raw / 15.0) * 100.0))
        else:
            pct = int(round((batt_raw / 255.0) * 100.0))
        return clip(pct, 0, 100)
    a = batt_map.get("a", 6.67)  # if batt_raw in 0..15, ~6.67 per step
    b = batt_map.get("b", 0.0)
    pct = int(round(a * batt_raw + b))
    return clip(pct, 0, 100)


def stable(buf: deque, s_thresh: float, slope_thresh: float) -> bool:
    if len(buf) < buf.maxlen:
        return False
    # population stdev over raw counts
    sd = stats.pstdev(buf)
    slope = (buf[-1] - buf[0]) / max(1, len(buf) - 1)
    return sd < s_thresh and abs(slope) < slope_thresh


async def run_scanner(callback, scan_time: float = None):
    """Start a BleakScanner with a detection callback. If scan_time is provided,
    run for that many seconds; else run until cancelled from outside."""
    async with BleakScanner(detection_callback=callback) as scanner:
        if scan_time is None:
            # Run indefinitely; caller should cancel task
            while True:
                await _asyncio.sleep(3600)
        else:
            await _asyncio.sleep(scan_time)
    return


async def collect_mean_raw(target_mac: Optional[str], target_uuid: str, *, seconds: float, endian: str,
                           debug: bool = False) -> int:
    """Collect raw readings for 'seconds' and return integer mean of raw."""
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
            return
        try:
            fields = parse_service_data(sd, endian=endian, dump_u16=False)
            raws.append(fields["raw"])
            if debug:
                print(f"[collect] raw={fields['raw']} b12=0x{fields['b12']:02x} b13=0x{fields['b13']:02x}")
        except Exception as e:
            if debug:
                print(f"[collect] parse error: {e}")

    task = _asyncio.create_task(run_scanner(on_detect, scan_time=seconds))
    with suppress(Exception):
        await task
    if not raws:
        raise RuntimeError("No samples captured during calibration window. Check MAC/UUID/endian.")
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
    parser_meta = cal.get("parser", {})
    if parser_meta:
        # warn if mismatch in debug
        if args.debug and (
            parser_meta.get("endian") != args.endian
            or parser_meta.get("raw_bytes") != [12, 13]
            or parser_meta.get("uuid", DEFAULT_SERVICE_UUID) != args.uuid
        ):
            print("[warn] Parser settings differ from saved calibration; weights may be off.")

    displayed_kg = 0.0
    zero_offset = 0.0 if not args.zero else (0.0)  # maintained as kg after conversion
    raw_buf = deque(maxlen=max(3, args.smooth))

    def on_detect(device, adv):
        nonlocal displayed_kg, zero_offset
        if args.mac:
            if device.address.replace(":", "").lower() != args.mac.replace(":", "").lower():
                return
        sd = (adv.service_data or {}).get(args.uuid)
        if not sd:
            return
        try:
            fields = parse_service_data(sd, endian=args.endian, dump_u16=args.dump_u16)

            # derived mappings
            temp_c_mapped = map_temp(fields["temp_c"], temp_map)
            batt_pct = map_batt(fields["batt_raw"], batt_map)

            # smoothing on RAW first
            raw_buf.append(fields["raw"])
            raw_med = stats.median(raw_buf)

            if slope is not None and intercept is not None:
                kg_now = slope * raw_med + intercept
                if args.temp_comp and cal.get("temp_ref") is not None:
                    kg_now -= cal.get("temp_coeff", 0.0) * (temp_c_mapped - cal.get("temp_ref", temp_c_mapped))
            else:
                kg_now = 0.0  # uncalibrated

            if args.zero:
                if zero_offset == 0.0:
                    zero_offset = kg_now
                kg_zeroed = max(0.0, kg_now - zero_offset)
            else:
                kg_zeroed = kg_now

            # stability gate
            commit = stable(raw_buf, s_thresh=args.stable_sd, slope_thresh=args.stable_slope)

            if commit:
                displayed_kg = kg_zeroed

            if args.print_raw:
                rssi = getattr(adv, "rssi", None)
                idle = "idle"  # placeholder; you can compute accel if you later add it
                tstamp = now_iso()
                if args.debug:
                    print(
                        f"{tstamp}  kg={displayed_kg:.3f} zeroed={(kg_zeroed if args.zero else 0):.3f} "
                        f"raw={int(raw_med)} temp={temp_c_mapped:.1f}°C rssi={rssi}dBm batt={batt_pct}%  "
                        f"b12=0x{fields['b12']:02x} b13=0x{fields['b13']:02x} {idle}"
                    )
                else:
                    print(
                        f"{tstamp}  kg={displayed_kg:.3f} zeroed={(kg_zeroed if args.zero else 0):.3f} "
                        f"raw={int(raw_med)}  temp={temp_c_mapped:.1f}°C  batt={batt_pct}%"
                    )

        except Exception as e:
            if args.debug:
                print(f"[read] parse error: {e}")

    # Run until Ctrl+C
    print("🔍 Listening for BLE advertisements... (Ctrl+C to stop)")
    try:
        await run_scanner(on_detect, scan_time=None)
    except KeyboardInterrupt:
        pass


async def calibrate(args):
    print("🧪 Calibration mode")
    print("1) Leave the scale EMPTY and press Enter. I will average raw for a few seconds...")
    input()
    R0 = await collect_mean_raw(args.mac, args.uuid, seconds=args.cal_seconds, endian=args.endian, debug=args.debug)
    print(f"   -> Empty mean raw = {R0}")

    print(f"2) Place known mass ({args.calibrate:.3f} kg), wait for it to settle, then press Enter.")
    input()
    R1 = await collect_mean_raw(args.mac, args.uuid, seconds=args.cal_seconds, endian=args.endian, debug=args.debug)
    print(f"   -> Loaded mean raw = {R1}")

    if R1 == R0:
        raise RuntimeError("Calibration failed: R1 equals R0; no delta.")

    slope = float(args.calibrate) / float(R1 - R0)
    intercept = -slope * R0

    cal = load_cal(args.load_cal or args.save_cal)
    cal.update({
        "slope": slope,
        "intercept": intercept,
        "temp_ref": 20.0,
        "temp_coeff": cal.get("temp_coeff", 0.0),
        "parser": {
            "endian": args.endian,
            "raw_bytes": [12, 13],
            "uuid": args.uuid,
        },
        # keep any existing mappings if present
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
    p.add_argument("--endian", choices=["little", "big"], default="little", help="Endian for raw bytes 12-13")
    p.add_argument("--smooth", type=int, default=5, help="Median smoothing window (raw counts)")
    p.add_argument("--zero", action="store_true", help="Start in zeroed (tare) mode")


    p.add_argument("--print-raw", action="store_true", help="Print live rows of parsed output")
    p.add_argument("--debug", action="store_true", help="Verbose debug including parse info")
    p.add_argument("--dump-u16", action="store_true", help="Add raw_le/raw_be to parse (debug only)")

    p.add_argument("--calibrate", type=float, default=None, help="Enter calibration mode with known mass (kg)")
    p.add_argument("--cal-seconds", type=float, default=4.0, help="Seconds to average raw during each cal step")
    p.add_argument("--save-cal", default="keg_cal.json", help="Path to save calibration JSON")
    p.add_argument("--load-cal", default=None, help="Path to load calibration JSON (else will use --save-cal if exists)")

    p.add_argument("--temp-comp", action="store_true", help="Enable temp compensation using JSON temp_coeff & temp_ref")
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
