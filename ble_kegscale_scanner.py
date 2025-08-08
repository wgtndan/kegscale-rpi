#!/usr/bin/env python3
"""
kegscale_scanner.py
-------------------
BLE scanner for the E4BE keg scale beacons with decoding of:
- Accelerated flag (button-press fast advertising)
- Battery percentage
- Temperature (°C)
- Sequence/counter
- Weight raw (u16) + placeholder linear calibration to kg
- State/range code (u16)

Tested with Bleak 1.0+ on Raspberry Pi (Linux).

Usage:
  python3 kegscale_scanner.py
  python3 kegscale_scanner.py --mac 5C:01:3B:35:92:EE
  python3 kegscale_scanner.py --log-file beacons.ndjson

Install:
  pip3 install bleak

Notes:
- We filter by the custom service UUID "0000e4be-0000-1000-8000-00805f9b34fb".
- The payload for this UUID has been observed as 17 bytes long.
- If you need systemd service, wrap this script and handle stdout/stderr as needed.
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

from bleak import BleakScanner, AdvertisementData

E4BE_UUID = "0000e4be-0000-1000-8000-00805f9b34fb"


@dataclass
class WeightCal:
    """Linear calibration: kg = a * raw + b"""
    a: float = 0.0
    b: float = 0.0


@dataclass
class DecodedFrame:
    accelerated: bool
    battery_pct: int
    temp_c: float
    seq: int
    weight_raw_u16: int
    state_code_u16: int
    checksum_u8: int
    raw_hex: str


def _u16be(b: bytes) -> int:
    return (b[0] << 8) | b[1]


def _u32be(b: bytes) -> int:
    return (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]


def decode_payload(payload: bytes) -> DecodedFrame:
    """
    Decode the 17-byte service data payload we've characterized.

    Byte layout (0-indexed):
      0: flags/frame (0x20 normal, 0x21 accelerated -> bit0 = accelerated)
      1: 0x00 (reserved)
      2: subtype/status (often 0x0F)
      3: battery % (0..100)
      4: temp_hi/status nibble (often 0x02)
      5: temp_lo as deci-C (e.g., 0xDA -> 21.8°C)
      6..9: u32 counter/sequence (big-endian)
      10..11: padding/reserved (often zeros)
      12..13: weight_raw candidate (u16 big-endian) -> correlates with app kg
      14..15: state/range code (u16 big-endian) -> e.g., 0x0101, 0x0102
      16: checksum/status (u8) (TBD)
    """
    if len(payload) != 17:
        raise ValueError(f"Expected 17-byte payload, got {len(payload)}")

    b = payload
    accelerated = bool(b[0] & 0x01)
    battery_pct = b[3]
    temp_c = b[5] / 10.0  # empirically observed
    seq = _u32be(b[6:10])
    weight_raw_u16 = _u16be(b[12:14])
    state_code_u16 = _u16be(b[14:16])
    checksum_u8 = b[16]

    return DecodedFrame(
        accelerated=accelerated,
        battery_pct=battery_pct,
        temp_c=temp_c,
        seq=seq,
        weight_raw_u16=weight_raw_u16,
        state_code_u16=state_code_u16,
        checksum_u8=checksum_u8,
        raw_hex=payload.hex(),
    )


def apply_weight_calibration(raw: int, cal: WeightCal | None) -> float | None:
    """Convert weight_raw to kg using a simple linear model if provided."""
    if cal is None:
        return None
    return cal.a * raw + cal.b


def ndjson_dump(fp, obj: dict):
    fp.write(json.dumps(obj, separators=(",", ":")) + "\n")
    fp.flush()


async def main():
    parser = argparse.ArgumentParser(description="Scan and decode keg scale E4BE beacons.")
    parser.add_argument("--mac", help="Filter for a specific MAC (case-insensitive).")
    parser.add_argument("--uuid", default=E4BE_UUID, help="Service UUID to parse (default: E4BE).")
    parser.add_argument("--log-file", help="Write NDJSON lines to this file.")
    parser.add_argument("--print-raw", action="store_true", help="Include raw Bleak advertisement fields in stdout.")
    parser.add_argument("--cal-a", type=float, default=None, help="Weight calibration slope a (kg per raw).")
    parser.add_argument("--cal-b", type=float, default=None, help="Weight calibration intercept b (kg at raw=0).")
    args = parser.parse_args()

    mac_filter = args.mac.upper() if args.mac else None
    cal = None
    if args.cal_a is not None and args.cal_b is not None:
        cal = WeightCal(a=args.cal_a, b=args.cal_b)

    log_fp = open(args.log_file, "a", buffering=1) if args.log_file else None

    def detection_callback(device, adv: AdvertisementData):
        # Filter MAC if requested
        if mac_filter and (device.address or "").upper() != mac_filter:
            return

        # Grab service data payload for the UUID
        payload = None
        if adv.service_data and args.uuid in adv.service_data:
            payload = adv.service_data[args.uuid]

        if not payload:
            return

        # Expect 17-byte payload
        try:
            decoded = decode_payload(payload)
        except Exception as e:
            # Ignore malformed
            return

        # Assemble record
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "ts_iso": now,
            "mac": device.address,
            "rssi": adv.rssi,
            "rssi_source": "adv",
            "uuid": args.uuid,
            "raw_hex": decoded.raw_hex,
            "len": len(payload),
            "accelerated": decoded.accelerated,
            "battery_pct": decoded.battery_pct,
            "temperature_c": decoded.temp_c,
            "seq": decoded.seq,
            "weight_raw_u16": decoded.weight_raw_u16,
            "weight_kg": apply_weight_calibration(decoded.weight_raw_u16, cal),
            "state_code_u16": decoded.state_code_u16,
            "checksum_u8": decoded.checksum_u8,
        }

        # Print to stdout
        print(json.dumps(record, separators=(",", ":")))

        # And to file if requested
        if log_fp:
            ndjson_dump(log_fp, record)

        # Optionally dump raw advertisement fields (debug)
        if args.print_raw:
            dbg = {
                "local_name": adv.local_name,
                "service_uuids": adv.service_uuids,
                "tx_power": adv.tx_power,
                "manufacturer_data": {
                    k: v.hex() if isinstance(v, (bytes, bytearray)) else v
                    for k, v in (adv.manufacturer_data or {}).items()
                },
            }
            sys.stderr.write(json.dumps(dbg, indent=2) + "\n")

    # Use BleakScanner with detection callback (Bleak 1.0+ API)
    scanner = BleakScanner(detection_callback=detection_callback, service_uuids=[args.uuid])

    print("🔍 Listening for BLE advertisements... (Ctrl+C to stop)")
    try:
        await scanner.start()
        while True:
            await asyncio.sleep(0.3)
    except KeyboardInterrupt:
        pass
    finally:
        await scanner.stop()
        if log_fp:
            log_fp.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as e:
        # In case of event loop already running (rare), fallback
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
