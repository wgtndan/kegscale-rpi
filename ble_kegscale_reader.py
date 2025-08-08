#!/usr/bin/env python3
import argparse
import asyncio as _asyncio
import json
import signal
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Deque, Tuple, List
from collections import deque

from bleak import BleakScanner

SERVICE_UUID = "0000e4be-0000-1000-8000-00805f9b34fb"  # custom service in ADV service_data

# ---------------------------
# Parsing / math helpers
# ---------------------------

@dataclass
class Packet:
    ts: float
    raw_hex: str
    payload: bytes
    flags: int
    batt_raw: int
    temp_deci_c: int
    weight_raw_u16: int   # << NEW: bytes 8-9 LE
    seq_u16_le: int       # suspected timer/seq: bytes 12-13 LE
    seq_u16_be: int       # bytes 12-13 BE
    accel: bool

def parse_payload(raw_hex: str) -> Optional[Packet]:
    try:
        b = bytes.fromhex(raw_hex)
    except Exception:
        return None
    if len(b) < 17:
        return None

    flags = b[0]
    accel = bool(flags & 0x01)  # bit0 toggles in your logs

    batt_raw = b[2]            # app seems ~67% when this byte ~15 and max ~22
    temp_deci_c = b[5]         # deci°C (18 -> 1.8°C? Your device looks like 188=18.8; but logs show 0x0f3c?)
    # In samples you posted, temp was b[5]==0x3c? Those were earlier firmwares.
    # Recent lines show temp toggling via b[5]==0x40 (64 deciC=6.4). We'll keep b[5] as deci C like before.

    # --- KEY CHANGE: weight source ---
    # Weight is the u16 little-endian at offsets 8-9.
    # Your dumps: ... 0000 [05 bb] 0001 [35 17] ...
    #  - 0x05BB moves with load, not monotonic with time.
    #  - 0x3517 monotonic timer -> not weight.
    weight_raw_u16 = b[8] | (b[9] << 8)  # LE

    # Keep the former suspect as seq for debugging (bytes 12-13).
    seq_le = b[12] | (b[13] << 8)
    seq_be = (b[12] << 8) | b[13]

    return Packet(
        ts=time.time(),
        raw_hex=raw_hex,
        payload=b,
        flags=flags,
        batt_raw=batt_raw,
        temp_deci_c=temp_deci_c,
        weight_raw_u16=weight_raw_u16,
        seq_u16_le=seq_le,
        seq_u16_be=seq_be,
        accel=accel,
    )

@dataclass
class Cal:
    slope: float   # kg per raw unit
    intercept: float  # kg

    def to_json(self):
        return {"slope": self.slope, "intercept": self.intercept}

    @staticmethod
    def from_json(d):
        return Cal(float(d["slope"]), float(d["intercept"]))


# ---------------------------
# Live scanner
# ---------------------------

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")

def fmt_batt_pct(b_raw: int, batt_max: int) -> int:
    pct = int(round(100 * max(0, min(b_raw, batt_max)) / batt_max))
    return pct

class StopSignal(Exception):
    pass

async def stream_packets(mac_filter: Optional[str], uuid: str, queue: _asyncio.Queue, debug=False):
    mac_norm = mac_filter.lower() if mac_filter else None

    def cb(device, adv):
        if mac_norm and device.address.lower() != mac_norm:
            return
        sd = adv.service_data or {}
        data = sd.get(uuid)
        if not data:
            return
        raw_hex = data.hex()
        pkt = parse_payload(raw_hex)
        if not pkt:
            return
        if debug:
            # also expose advertisement meta quickly
            pass
        queue.put_nowait(pkt)

    scanner = BleakScanner(detection_callback=cb)
    try:
        await scanner.start()
        while True:
            await _asyncio.sleep(0.05)
    finally:
        await scanner.stop()

async def collect_mean_raw(mac_filter: Optional[str], uuid: str, seconds: float, debug=False) -> int:
    q: _asyncio.Queue = _asyncio.Queue()
    task = _asyncio.create_task(stream_packets(mac_filter, uuid, q, debug=debug))
    t0 = time.time()
    vals: List[int] = []
    try:
        while time.time() - t0 < seconds:
            try:
                pkt: Packet = await _asyncio.wait_for(q.get(), timeout=0.8)
            except _asyncio.TimeoutError:
                continue
            vals.append(pkt.weight_raw_u16)
    finally:
        task.cancel()
        with _asyncio.suppress(Exception):
            await task
    if not vals:
        raise RuntimeError("No samples captured during calibration window.")
    return int(round(statistics.mean(vals)))

# ---------------------------
# Main
# ---------------------------

def build_argparser():
    ap = argparse.ArgumentParser(description="BLE keg scale reader (weight from bytes 8-9 LE).")
    ap.add_argument("--mac", help="Filter to this MAC (recommended).")
    ap.add_argument("--uuid", default=SERVICE_UUID, help="Service Data UUID to parse (default keg scale UUID).")
    ap.add_argument("--smooth", type=int, default=1, help="Moving average window over weight_raw (packets).")
    ap.add_argument("--zero", action="store_true", help="Print weight zeroed to first sample.")
    ap.add_argument("--print-raw", action="store_true", help="Include raw_hex and byte hints.")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--dump-u16", action="store_true", help="Print u16 fields at [8,10,12,14] (LE/BE).")

    # Battery/temp tweaks
    ap.add_argument("--batt-max", type=int, default=22, help="Battery full-scale raw value (default 22).")
    ap.add_argument("--temp-offset", type=float, default=0.0, help="Temperature additive offset in °C.")
    # Calibration
    ap.add_argument("--calibrate", type=float, nargs="?", const=1.13, help="Run two-step calibration with known mass (kg). Default 1.13 if no value given.")
    ap.add_argument("--cal-seconds", type=float, default=3.0, help="Seconds to average for each cal step.")
    ap.add_argument("--save-cal", help="File to save calibration JSON (slope/intercept).")
    ap.add_argument("--load-cal", help="File to load calibration JSON.")
    return ap

def apply_cal(raw: int, cal: Optional[Cal]) -> Optional[float]:
    if cal is None:
        return None
    return cal.slope * raw + cal.intercept

async def run_live(args, cal: Optional[Cal]):
    q: _asyncio.Queue = _asyncio.Queue()
    task = _asyncio.create_task(stream_packets(args.mac, args.uuid, q, debug=args.debug))

    # smoothing window
    win: Deque[int] = deque(maxlen=max(1, args.smooth))
    zero_offset: Optional[float] = None
    dump_once = 0

    try:
        while True:
            pkt: Packet = await q.get()

            win.append(pkt.weight_raw_u16)
            raw_avg = sum(win) / len(win)

            kg = apply_cal(raw_avg, cal)
            if args.zero:
                if zero_offset is None and kg is not None:
                    zero_offset = kg
                kg_zero = (kg - zero_offset) if (kg is not None and zero_offset is not None) else None
            else:
                kg_zero = kg

            batt_pct = fmt_batt_pct(pkt.batt_raw, args.batt_max)
            temp_c = pkt.temp_deci_c / 10.0 + args.temp_offset

            ts = datetime.fromtimestamp(pkt.ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
            base = f"{ts}  kg={kg:6.3f}" if kg is not None else f"{ts}  kg=   n/a"
            base += f" zeroed={kg_zero:6.3f}" if kg_zero is not None else " zeroed=   n/a"
            base += f"  raw={int(raw_avg):5d}  temp={temp_c:.1f}°C  rssi=?dBm  batt={batt_pct}%"
            base += "  ACCEL" if pkt.accel else "  idle"

            dump_line = ""
            if args.print_raw:
                b12 = pkt.payload[12] if len(pkt.payload) > 12 else 0
                b13 = pkt.payload[13] if len(pkt.payload) > 13 else 0
                dump_line += f"  raw_hex={pkt.raw_hex}  b12=0x{b12:02x} b13=0x{b13:02x}"

            if args.dump_u16:
                b = pkt.payload
                def u16le(i): return (b[i] | (b[i+1]<<8)) if i+1 < len(b) else None
                def u16be(i): return ((b[i]<<8) | b[i+1]) if i+1 < len(b) else None
                fields = []
                for i in (8,10,12,14):
                    le = u16le(i); be = u16be(i)
                    fields.append(f"u16@{i}(le)={le:5d}" if le is not None else f"u16@{i}(le)=  N/A")
                    fields.append(f"u16@{i}(be)={be:5d}" if be is not None else f"u16@{i}(be)=  N/A")
                dump_line += "  [" + "  ".join(fields) + "]"

            print(base + ("" if not dump_line else "  " + dump_line))
    finally:
        task.cancel()
        with _asyncio.suppress(Exception):
            await task

async def main():
    ap = build_argparser()
    args = ap.parse_args()

    # calibration modes
    if args.calibrate is not None:
        print("🧪 Calibration mode")
        input("1) Leave the scale EMPTY and press Enter. I will average raw for a few seconds...")
        try:
            R0 = await collect_mean_raw(args.mac, args.uuid, seconds=args.cal_seconds, debug=args.debug)
        except RuntimeError as e:
            print(f"ERROR: {e}\nHint: press the device's accelerate button during capture, verify MAC/UUID, move closer.", file=sys.stderr)
            return
        print(f"   Empty mean raw = {R0}")

        known = args.calibrate
        input(f"2) Place the known mass (KG={known}) and press Enter. Averaging again...")
        try:
            R1 = await collect_mean_raw(args.mac, args.uuid, seconds=args.cal_seconds, debug=args.debug)
        except RuntimeError as e:
            print(f"ERROR: {e}\nHint: press the device's accelerate button during capture, verify MAC/UUID, move closer.", file=sys.stderr)
            return
        print(f"   Loaded mean raw = {R1}")

        # Fit kg = m * raw + b ; with points (R0, 0) and (R1, known)
        if R1 == R0:
            print("Calibration failed: identical raw values. Try again.", file=sys.stderr)
            return
        m = known / (R1 - R0)
        b = -m * R0
        print(f"✅ Calibration complete: slope={m:.8f}, intercept={b:.8f}")
        cal = Cal(m, b)
        if args.save_cal:
            with open(args.save_cal, "w") as f:
                json.dump(cal.to_json(), f, indent=2)
            print(f"💾 Saved calibration to {args.save_cal}")
        print("Starting live read with new calibration...\n")
        await run_live(args, cal)
        return

    # load existing cal if provided
    cal = None
    if args.load_cal:
        with open(args.load_cal, "r") as f:
            cal = Cal.from_json(json.load(f))
        print(f"Loaded calibration: slope={cal.slope:.8f}, intercept={cal.intercept:.8f}")

    await run_live(args, cal)


if __name__ == "__main__":
    try:
        _asyncio.run(main())
    except KeyboardInterrupt:
        pass
