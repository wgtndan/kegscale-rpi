#!/usr/bin/env python3
"""
ble_kegscale_scanner_tare.py
----------------------------
Robust E4BE BLE scanner with *tare-based linear* weight model.

Formula:
  kg = K * (raw - baseline[state])

Where:
- baseline[state] is captured when --tare is enabled (first packet seen per state),
  and can be persisted with --tare-file.
- K (kg per raw count) is provided via --k, or learned once via --learn-k <known_kg>
  after tare (we compute K = known_kg / (raw - baseline[state]) on the first packet
  that has a nonzero delta). You can persist K with --k-file.

Why this model:
- Handles baseline shifts between runs and across device "states"/ranges.
- Simpler & more stable if sensors are potentiometers (near-linear).
- Meets ~±100 g accuracy with minimal setup (tare + one known weight).

Usage examples:
  # 1) Tare (empty scale) and save baselines
  python3 ble_kegscale_scanner_tare.py --tare --tare-file tare.json --log-file live.ndjson

  # 2) Place a known 2.00 kg weight and learn K automatically; persist it
  python3 ble_kegscale_scanner_tare.py --tare --tare-file tare.json \
      --learn-k 2.00 --k-file k.json --log-file live.ndjson

  # 3) Normal operation using saved tare + K
  python3 ble_kegscale_scanner_tare.py --tare-file tare.json --k-file k.json --log-file live.ndjson

Notes:
- You can also pass --k directly without learning.
- If a new state appears later, we'll use the same K and the per-state baseline.
- If baseline[state] is missing, weight_kg will be null until that state is tared.
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from bleak import BleakScanner, AdvertisementData

E4BE_UUID_128 = "0000e4be-0000-1000-8000-00805f9b34fb"
E4BE_UUID_16  = "e4be"


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
    if len(payload) != 17:
        raise ValueError(f"Expected 17-byte payload, got {len(payload)}")

    b = payload
    accelerated = bool(b[0] & 0x01)   # 0x20 normal, 0x21 accelerated (bit0)
    battery_pct = b[3]                # byte 3
    temp_c = b[5] / 10.0              # byte 5 deci-C
    seq = _u32be(b[6:10])             # bytes 6..9 u32 BE
    weight_raw_u16 = _u16be(b[12:14]) # bytes 12..13 u16 BE
    state_code_u16 = _u16be(b[14:16]) # bytes 14..15 u16 BE
    checksum_u8 = b[16]               # byte 16

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


def _find_e4be_payload(adv: AdvertisementData, want_uuid: str | None, debug: bool = False) -> Optional[bytes]:
    if not adv.service_data:
        return None

    want = (want_uuid or "").lower()
    candidates = []
    for key, val in adv.service_data.items():
        k = str(key).lower()
        if want:
            if k == want or k.endswith(want) or want.endswith(k):
                candidates.append(val)
                continue
        if k == E4BE_UUID_128 or k.endswith(E4BE_UUID_128) or k == E4BE_UUID_16 or k.endswith(E4BE_UUID_16):
            candidates.append(val)

    for v in candidates:
        if isinstance(v, (bytes, bytearray)) and len(v) == 17:
            return bytes(v)

    for v in adv.service_data.values():
        if isinstance(v, (bytes, bytearray)) and len(v) == 17:
            if debug:
                sys.stderr.write("⚠️  Using 17-byte fallback (no UUID key match).\n")
            return bytes(v)

    return None


def load_json(path: Optional[str]) -> Optional[dict]:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        with open(p, "r") as f:
            return json.load(f)
    except Exception as e:
        sys.stderr.write(f"⚠️ Failed to load {path}: {e}\n")
        return None


def save_json(path: Optional[str], data: dict):
    if not path:
        return
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        sys.stderr.write(f"⚠️ Failed to save {path}: {e}\n")


async def main():
    parser = argparse.ArgumentParser(description="Scan and decode keg scale E4BE beacons (tare-based model).")
    parser.add_argument("--mac", help="Filter for a specific MAC (case-insensitive).")
    parser.add_argument("--uuid", default=E4BE_UUID_128, help="Service UUID to parse (128- or 16-bit ok).")
    parser.add_argument("--log-file", help="Write NDJSON lines to this file.")
    parser.add_argument("--print-raw", action="store_true", help="Include raw Bleak advertisement fields in stderr.")
    parser.add_argument("--debug", action="store_true", help="Extra diagnostics to stderr.")

    # Tare-based model flags
    parser.add_argument("--tare", action="store_true", help="Capture per-state baseline from first packets.")
    parser.add_argument("--tare-file", help="Persist/load per-state baselines JSON.")
    parser.add_argument("--k", type=float, default=None, help="Slope K (kg per raw count).")
    parser.add_argument("--k-file", help="Persist/load K value JSON (e.g., {\"K\": 0.000445}).")
    parser.add_argument("--learn-k", type=float, default=None, metavar="KNOWN_KG",
                        help="Learn K from the first packet after tare: K = KNOWN_KG/(raw - baseline[state]).")

    args = parser.parse_args()

    mac_filter = args.mac.upper() if args.mac else None

    # Load persisted baselines and K if provided
    baselines: Dict[str, int] = load_json(args.tare_file) or {}
    k_store = load_json(args.k_file) or {}
    K: Optional[float] = args.k if args.k is not None else k_store.get("K")

    if args.debug:
        sys.stderr.write(f"Loaded baselines: {baselines}\n")
        sys.stderr.write(f"Loaded K: {K}\n")

    log_fp = open(args.log_file, "a", buffering=1) if args.log_file else None

    # Keep track if we already learned K this run
    learned_k = False

    def maybe_save_baselines():
        if args.tare_file:
            save_json(args.tare_file, baselines)

    def maybe_save_K():
        nonlocal K
        if args.k_file and K is not None:
            save_json(args.k_file, {"K": K})

    def detection_callback(device, adv: AdvertisementData):
        nonlocal K, learned_k
        try:
            if mac_filter and (device.address or "").upper() != mac_filter:
                return

            payload = _find_e4be_payload(adv, args.uuid, debug=args.debug)
            if not payload:
                if args.debug and adv.service_data:
                    sys.stderr.write(f"no-match svc_data keys={list(adv.service_data.keys())}\n")
                return

            decoded = decode_payload(payload)

            # Tare logic: capture baseline per state once
            state_hex = f"0x{decoded.state_code_u16:04x}"
            if args.tare and state_hex not in baselines:
                baselines[state_hex] = int(decoded.weight_raw_u16)
                if args.debug:
                    sys.stderr.write(f"TARE captured for {state_hex}: baseline={baselines[state_hex]}\n")
                maybe_save_baselines()

            # Learn-K logic: compute K once when we see a delta
            kg_pred = None
            if args.learn_k is not None and not learned_k:
                base = baselines.get(state_hex)
                if base is not None:
                    delta = int(decoded.weight_raw_u16) - int(base)
                    if delta != 0:
                        K = float(args.learn_k) / float(delta)
                        learned_k = True
                        if args.debug:
                            sys.stderr.write(f"LEARNED K: {K} kg/count using state {state_hex}, baseline={base}, raw={decoded.weight_raw_u16}\n")
                        maybe_save_K()

            # Compute weight if we have both baseline and K
            base = baselines.get(state_hex)
            if base is not None and K is not None:
                kg_pred = K * (float(decoded.weight_raw_u16) - float(base))

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
                "weight_kg": kg_pred,
                "state_code_u16": decoded.state_code_u16,
                "checksum_u8": decoded.checksum_u8,
            }

            print(json.dumps(record, separators=(",", ":")))

            if log_fp:
                log_fp.write(json.dumps(record, separators=(",", ":")) + "\n")
                log_fp.flush()

            if args.print_raw or args.debug:
                dbg = {
                    "local_name": adv.local_name,
                    "service_uuids": adv.service_uuids,
                    "tx_power": adv.tx_power,
                    "manufacturer_data_keys": list((adv.manufacturer_data or {}).keys()),
                }
                sys.stderr.write(json.dumps(dbg) + "\n")

        except Exception as e:
            if args.debug:
                sys.stderr.write(f"decode error: {e}\n")

    scanner = BleakScanner(detection_callback=detection_callback)

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
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
