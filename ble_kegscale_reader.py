#!/usr/bin/env python3
"""
ble_kegscale_reader.py
----------------------
Lightweight BLE reader for your keg scale beacons.

Decoding:
- Weight raw field = bytes 12..13 (u16 big-endian) from the 17-byte service data.
- Weight (kg) computed via linear model: kg = slope * raw + intercept.
  Defaults come from your fit:
      slope    = -0.00169562
      intercept=  37.41647250
- Negative slope is expected (raw decreases as mass increases).

Extras:
- --smooth N: rolling average of raw before applying model (default 7).
- --zero: capture first computed kg as baseline and print zeroed_kg = kg - kg0.
- Clean, single-line status output. Optional NDJSON logging.

Examples:
  # Just read with defaults
  python3 ble_kegscale_reader.py --mac 5C:01:3B:35:92:EE

  # Zero at start and smooth more
  python3 ble_kegscale_reader.py --mac 5C:01:3B:35:92:EE --zero --smooth 10

  # Override model
  python3 ble_kegscale_reader.py --mac 5C:01:3B:35:92:EE --slope -0.00170 --intercept 37.50
"""

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
    accelerated = bool(b[0] & 0x01)   # 0x20 normal, 0x21 accelerated
    battery_pct = b[3]
    temperature_c = b[5] / 10.0
    seq_u32 = _u32be(b[6:10])
    raw12_13 = _u16be(b[12:14])       # <-- weight raw
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


def find_payload(adv: AdvertisementData, want_uuid: Optional[str], debug: bool=False) -> Optional[bytes]:
    if not adv.service_data:
        return None

    candidates = []
    want = (want_uuid or "").lower()

    for key, val in adv.service_data.items():
        k = str(key).lower()
        if want:
            if k == want or k.endswith(want) or want.endswith(k):
                if isinstance(val, (bytes, bytearray)) and len(val) == 17:
                    return bytes(val)
        # fallback match on the known UUIDs
        if k == E4BE_UUID_128 or k.endswith(E4BE_UUID_128) or k == E4BE_UUID_16 or k.endswith(E4BE_UUID_16):
            if isinstance(val, (bytes, bytearray)) and len(val) == 17:
                candidates.append(bytes(val))

    if candidates:
        return candidates[0]

    # final fallback: any 17B service_data blob
    for v in adv.service_data.values():
        if isinstance(v, (bytes, bytearray)) and len(v) == 17:
            if debug:
                sys.stderr.write("⚠️  Using 17-byte fallback (no UUID key match)\n")
            return bytes(v)
    return None


async def main():
    ap = argparse.ArgumentParser(description="BLE kegscale reader with linear weight model and smoothing.")
    ap.add_argument("--mac", help="Filter to a specific MAC (case-insensitive).")
    ap.add_argument("--uuid", default=E4BE_UUID_128, help="Service UUID key to parse (128- or 16-bit ok).")
    ap.add_argument("--smooth", type=int, default=7, help="Rolling average window (on raw12_13) before model.")
    ap.add_argument("--zero", action="store_true", help="Zero output to first computed kg (prints zeroed_kg).")
    ap.add_argument("--slope", type=float, default=-0.00169562, help="Model slope (kg per raw count).")
    ap.add_argument("--intercept", type=float, default=37.41647250, help="Model intercept (kg).")
    ap.add_argument("--log-file", help="Write NDJSON to this file.")
    ap.add_argument("--debug", action="store_true", help="Extra diagnostics to stderr.")
    args = ap.parse_args()

    mac_filter = args.mac.upper() if args.mac else None

    raw_buf = deque(maxlen=max(args.smooth, 1))
    zero_kg: Optional[float] = None
    log_fp = open(args.log_file, "a", buffering=1) if args.log_file else None

    def fmt_line(ts, kg, kg0, raw, rssi, temp, accel, batt):
        if kg is None:
            wtxt = "kg=…"
            ztxt = "zeroed=…"
        else:
            wtxt = f"kg={kg:7.3f}"
            ztxt = f" zeroed={kg-kg0:7.3f}" if (args.zero and kg0 is not None) else ""
        return (f"{ts}  {wtxt}{ztxt}  raw={raw:5d}  temp={temp:4.1f}°C  "
                f"rssi={rssi:3d}dBm  batt={batt:2d}%  {'ACCEL' if accel else 'idle'}")

    def detection_callback(device, adv: AdvertisementData):
        nonlocal zero_kg

        # MAC filter
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
        line = fmt_line(now, kg, zero_kg, raw_smoothed, adv.rssi, d.temperature_c, d.accelerated, d.battery_pct)
        # pretty single-line update
        print("\r" + line, end="", flush=True)

        # NDJSON log (one per packet)
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
                "manufacturer_data_keys": list((adv.manufacturer_data or {}).keys()),
            }
            sys.stderr.write("\n" + json.dumps(dbg) + "\n")

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
        print()  # newline after last status line


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError:
        # If already in an event loop
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        loop.run_until_complete(main())
