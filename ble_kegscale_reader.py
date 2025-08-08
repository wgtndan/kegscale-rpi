#!/usr/bin/env python3
import argparse
import asyncio
import json
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Optional

from bleak import BleakScanner, AdvertisementData

E4BE_UUID_128 = "0000e4be-0000-1000-8000-00805f9b34fb"
E4BE_UUID_16  = "e4be"

@dataclass
class Decoded:
    accelerated: bool
    battery_pct: int
    temperature_c: float
    seq_u32: int
    raw12_13: int
    checksum_u8: int
    raw_hex: str

def _u16be(b: bytes) -> int:
    return (b[0] << 8) | b[1]

def _u32be(b: bytes) -> int:
    return (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]

def decode(payload: bytes) -> Decoded:
    if len(payload) != 17:
        raise ValueError(f"expected 17B payload, got {len(payload)}")
    b = payload
    # sanity: byte0 is flags: 0x20 normal, 0x21 accelerated
    accel_flag = b[0]
    accelerated = (accel_flag & 0x01) == 0x01
    battery_pct = b[3]
    # TEMP: we don't fully trust the position; keep it as b[5]/10 for display only
    temperature_c = b[5] / 10.0
    seq_u32 = _u32be(b[6:10])
    raw12_13 = _u16be(b[12:14])       # weight raw
    checksum_u8 = b[16]
    return Decoded(
        accelerated=accelerated,
        battery_pct=battery_pct,
        temperature_c=temperature_c,
        seq_u32=seq_u32,
        raw12_13=raw12_13,
        checksum_u8=checksum_u8,
        raw_hex=payload.hex(),
    )

def looks_like_e4be(payload: bytes) -> bool:
    if len(payload) != 17:
        return False
    b0 = payload[0]
    return b0 in (0x20, 0x21)  # observed flags

def find_payload(adv: AdvertisementData, want_uuid: Optional[str], debug: bool=False) -> Optional[bytes]:
    if not adv.service_data:
        return None
    want = (want_uuid or "").lower()

    # 1) Strict UUID match first
    for key, val in adv.service_data.items():
        k = str(key).lower()
        if want and (k == want or k.endswith(want) or want.endswith(k)):
            if isinstance(val, (bytes, bytearray)) and len(val) == 17:
                b = bytes(val)
                if looks_like_e4be(b):
                    return b

    # 2) Fuzzy match on known 'e4be' keys
    for key, val in adv.service_data.items():
        k = str(key).lower()
        if ("e4be" in k) and isinstance(val, (bytes, bytearray)) and len(val) == 17:
            b = bytes(val)
            if looks_like_e4be(b):
                return b

    # 3) As a last resort, scan any 17B blob but require the flags sanity
    for val in adv.service_data.values():
        if isinstance(val, (bytes, bytearray)) and len(val) == 17:
            b = bytes(val)
            if looks_like_e4be(b):
                if debug:
                    sys.stderr.write("⚠️  Using fallback 17B block that passes flags check\n")
                return b
    return None


async def collect_mean_raw(mac_filter, uuid_key, seconds=2.0, debug=False):
    """
    Listen for `seconds` and return the mean of raw12_13 for matching device.
    """
    from statistics import mean
    samples = []

    def cb(device, adv: AdvertisementData):
        if mac_filter and ((device.address or "").upper() != mac_filter):
            return
        payload = find_payload(adv, uuid_key, debug=debug)
        if not payload:
            return
        try:
            d = decode(payload)
        except Exception:
            return
        samples.append(d.raw12_13)

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    try:
        # sample a little longer than requested to ensure we have data
        await asyncio.sleep(max(0.5, seconds))
    finally:
        await scanner.stop()

    if not samples:
        raise RuntimeError("No samples captured during calibration window.")
    return int(round(mean(samples)))

async def main():
    ap = argparse.ArgumentParser(description="BLE kegscale reader with linear weight model and smoothing.")
    ap.add_argument("--mac", help="Filter to a specific MAC (case-insensitive).")
    ap.add_argument("--uuid", default=E4BE_UUID_128, help="Service UUID key to parse (128- or 16-bit ok).")
    ap.add_argument("--smooth", type=int, default=7, help="Rolling average window (on raw12_13) before model.")
    ap.add_argument("--zero", action="store_true", help="Zero output to first computed kg (prints zeroed_kg).")
    ap.add_argument("--slope", type=float, default=-0.00169562, help="Model slope (kg per raw count).")
    ap.add_argument("--intercept", type=float, default=37.41647250, help="Model intercept (kg).")
    ap.add_argument("--log-file", help="Write NDJSON to this file.")
    ap.add_argument("--print-raw", action="store_true", help="Also print raw_hex and bytes[12:14] for the first few packets.")
    ap.add_argument("--debug", action="store_true", help="Extra diagnostics to stderr.")
    ap.add_argument("--calibrate", type=float, metavar="KG", help="Guided two-point calibration with known mass KG.")
ap.add_argument("--cal-seconds", type=float, default=2.0, help="Seconds to average for each calibration capture.")
ap.add_argument("--save-cal", help="Write computed slope/intercept to this JSON file.")
ap.add_argument("--load-cal", help="Load slope/intercept from this JSON file (overrides defaults).")
args = ap.parse_args()

    mac_filter = args.mac.upper() if args.mac else None
if args.load_cal:
    try:
        with open(args.load_cal) as f:
            cal = json.load(f)
            if "slope" in cal: args.slope = float(cal["slope"])
            if "intercept" in cal: args.intercept = float(cal["intercept"])
            print(f"📥 Loaded calibration from {args.load_cal}: slope={args.slope:.8f}, intercept={args.intercept:.8f}")
    except Exception as e:
        print(f"⚠️  Could not load calibration: {e}", file=sys.stderr)



    # Guided calibration
    if args.calibrate is not None:
        print("🧪 Calibration mode")
        print("1) Leave the scale EMPTY and press Enter. I will average raw for a few seconds...")
        input()
        R0 = await collect_mean_raw(mac_filter, args.uuid, seconds=args.cal_seconds, debug=args.debug)
        print(f"   Empty mean raw = {R0}")

        print(f"2) Place the known mass (KG={args.calibrate:.3f}) and press Enter. Averaging again...")
        input()
        R1 = await collect_mean_raw(mac_filter, args.uuid, seconds=args.cal_seconds, debug=args.debug)
        print(f"   Loaded mean raw = {R1}")

        if R1 == R0:\n            raise RuntimeError("Calibration failed: raw did not change between empty and loaded.")

        slope = args.calibrate / (R1 - R0)
        intercept = -slope * R0
        args.slope, args.intercept = slope, intercept
        print(f"✅ Calibration complete: slope={slope:.8f}, intercept={intercept:.8f}")

        if args.save_cal:
            try:
                with open(args.save_cal, "w") as f:
                    json.dump({"slope": slope, "intercept": intercept}, f, indent=2)
                print(f"💾 Saved calibration to {args.save_cal}")
            except Exception as e:
                print(f"⚠️  Could not save calibration: {e}", file=sys.stderr)

        print("Starting live read with new calibration...\n")
    raw_buf = deque(maxlen=max(args.smooth, 1))
    zero_kg: Optional[float] = None
    log_fp = open(args.log_file, "a", buffering=1) if args.log_file else None

    dump_counter = 0

    def detection_callback(device, adv: AdvertisementData):
        nonlocal zero_kg, dump_counter

        if mac_filter and ((device.address or "").upper() != mac_filter):
            return

        payload = find_payload(adv, args.uuid, debug=args.debug)
        if not payload:
            return

        try:
            d = decode(payload)
        except Exception as e:
            if args.debug:
                sys.stderr.write(f"decode err: {e}\n")
            return

        raw_buf.append(d.raw12_13)
        raw_smoothed = int(round(mean(raw_buf))) if raw_buf else d.raw12_13
        kg = args.slope * float(raw_smoothed) + args.intercept
        if args.zero and zero_kg is None:
            zero_kg = kg

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        base = (f"{now}  kg={kg:7.3f}"
                f"{(f' zeroed={kg-zero_kg:7.3f}' if (args.zero and zero_kg is not None) else '')}"
                f"  raw={raw_smoothed:5d}  temp={d.temperature_c:4.1f}°C"
                f"  rssi={adv.rssi:3d}dBm  batt={d.battery_pct:2d}%  "
                f"{'ACCEL' if d.accelerated else 'idle'}")

        if args.print_raw and dump_counter < 10:
            # Show the two bytes we use for raw
            raw_b12 = payload[12]
            raw_b13 = payload[13]
            extra = f"  raw_hex={d.raw_hex}  b12={raw_b12:#04x} b13={raw_b13:#04x}"
            print(base + extra)
            dump_counter += 1
        else:
            print(base)

        if log_fp:
            rec = {
                "ts_iso": now,
                "mac": device.address,
                "rssi": adv.rssi,
                "uuid": args.uuid,
                "len": 17,
                "accelerated": d.accelerated,
                "battery_pct": d.battery_pct,
                "temperature_c": d.temperature_c,
                "seq": d.seq_u32,
                "raw12_13": d.raw12_13,
                "raw_smoothed": raw_smoothed,
                "kg": kg,
                "kg_zeroed": (kg - zero_kg) if (args.zero and zero_kg is not None) else None,
                "raw_hex": d.raw_hex,
                "checksum_u8": d.checksum_u8,
            }
            log_fp.write(json.dumps(rec, separators=(",", ":")) + "\n")

        if args.debug:
            dbg = {
                "local_name": adv.local_name,
                "service_uuids": adv.service_uuids,
                "tx_power": adv.tx_power,
                "service_data_keys": list(adv.service_data.keys()) if adv.service_data else [],
            }
            sys.stderr.write(json.dumps(dbg) + "\n")

    scanner = BleakScanner(detection_callback=detection_callback)
    print("🔍 Listening for BLE advertisements... (Ctrl+C to stop)")
    try:
        await scanner.start()
        while True:
            await asyncio.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        await scanner.stop()
        if log_fp:
            log_fp.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError:
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        loop.run_until_complete(main())
