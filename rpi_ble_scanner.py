#!/usr/bin/env python3
from __future__ import annotations
import argparse
import asyncio
import binascii
from collections import deque
from datetime import datetime
from statistics import median
from typing import Dict, Any, List, Optional
from bleak import BleakScanner

from ble_scanrecord import parse_scan_record
from kegscale_decode import decode_e4be, linear_weight_kg

UUID_E4BE = "0000e4be-0000-1000-8000-00805f9b34fb"

DEFAULT_TARE = 118_295
DEFAULT_SCALE = 0.000000045885  # kg per raw unit

def _norm_mac(s: str) -> str:
    return s.replace(":", "").lower()

def _merge_service_data(adv_obj) -> Dict[str, bytes]:
    merged: Dict[str, bytes] = {}
    for k, v in (adv_obj.service_data or {}).items():
        merged[str(k)] = bytes(v)
    for attr in ("advertisement_bytes", "scan_response"):
        raw = getattr(adv_obj, attr, None)
        if raw:
            sr = parse_scan_record(bytes(raw))
            for u, payload in sr.service_data.items():
                merged[str(u)] = bytes(payload)
    return merged

def _extract_extra_fields(payload: bytes) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if len(payload) > 9:
        out["seq"] = payload[9]
    if len(payload) > 12:
        out["marker12"] = payload[12]
    if len(payload) > 17:
        out["status"] = int.from_bytes(payload[16:18], "little", signed=False)
    return out

def _hampel_filter(values: List[int], k: int = 7, nsigma: float = 3.5) -> Optional[float]:
    """Return the latest value if it's within nsigma*MAD of the window median; else None."""
    if len(values) < 3:
        return float(values[-1]) if values else None
    m = median(values)
    abs_dev = [abs(x - m) for x in values]
    mad = median(abs_dev) or 1.0
    x = values[-1]
    if abs(x - m) <= nsigma * 1.4826 * mad:
        return float(x)
    return None

def make_callback(mac_target: str|None, uuid_filter: str|None, tare: int, scale: float, smooth_n: int, print_raw: bool, require_marker12: Optional[int], outlier_window: int, nsigma: float):
    mac_norm = _norm_mac(mac_target) if mac_target else None
    window_kg = deque(maxlen=max(1, smooth_n))
    window_raw = deque(maxlen=max(3, outlier_window))

    def cb(device, adv):
        if mac_norm and _norm_mac(device.address) != mac_norm:
            return
        service_data = _merge_service_data(adv)

        entries = []
        if uuid_filter:
            suf = uuid_filter[-8:].lower()
            for k, v in service_data.items():
                kl = k.lower()
                if kl == uuid_filter.lower() or kl.endswith(suf):
                    entries.append((k, v))
        else:
            entries = list(service_data.items())
        if not entries:
            return

        for uuid_str, payload in entries:
            if not uuid_str.lower().endswith(UUID_E4BE[-8:]):
                continue

            decoded = decode_e4be(payload)
            extra = _extract_extra_fields(payload)

            # Optional marker filter
            if require_marker12 is not None and extra.get("marker12") != require_marker12:
                continue

            parts = []
            if "temp_c" in decoded and decoded["temp_c"] is not None:
                parts.append(f"temp_c={decoded['temp_c']:.1f}")
            if "seq" in extra and extra["seq"] is not None:
                parts.append(f"seq={extra['seq']}")
            if "battery_raw" in decoded and decoded["battery_raw"] is not None:
                parts.append(f"battery_raw={decoded['battery_raw']}")
            if "marker12" in extra and extra["marker12"] is not None:
                parts.append(f"marker12=0x{extra['marker12']:02x}")
            if "status" in extra and extra["status"] is not None:
                parts.append(f"status=0x{extra['status']:04x}")

            wr = decoded.get("weight_raw")
            kg_inst = None
            avg_kg = None

            if wr is not None:
                parts.append(f"weight_raw={wr}")
                window_raw.append(wr)
                wr_ok = _hampel_filter(list(window_raw), k=outlier_window, nsigma=nsigma)
                if wr_ok is not None:
                    kg_inst = linear_weight_kg(int(wr_ok), tare, scale)
                    window_kg.append(kg_inst)
                    avg_kg = sum(window_kg) / len(window_kg)
                else:
                    parts.append("filtered=outlier")

            ts = datetime.now().isoformat(timespec="seconds")
            line = f"{ts} mac={device.address} rssi={adv.rssi} uuid={uuid_str} " + " ".join(parts)
            if kg_inst is not None:
                if smooth_n > 1:
                    line += f" weight_kg={kg_inst:.3f} avg_kg={avg_kg:.3f} (n={len(window_kg)})"
                else:
                    line += f" weight_kg={kg_inst:.3f}"
            print(line)

    return cb

async def main():
    ap = argparse.ArgumentParser(description="RPI BLE scanner with Android-style parsing, robust filtering, and calibration.")
    ap.add_argument("--mac", help="Target MAC to filter (e.g., 5C:01:3B:35:92:EE)")
    ap.add_argument("--uuid", default=UUID_E4BE, help="Service UUID filter (default E4BE) or 'all'")
    ap.add_argument("--tare", type=int, default=DEFAULT_TARE, help="Tare raw32 baseline")
    ap.add_argument("--scale", type=float, default=DEFAULT_SCALE, help="Scale factor (kg per raw unit)")
    ap.add_argument("--smooth", type=int, default=5, help="Rolling average window for weight display")
    ap.add_argument("--print-raw", action="store_true", help="Print raw service data hex")
    ap.add_argument("--adapter", default="hci0", help="HCI adapter to use (e.g., hci0, hci1)")
    ap.add_argument("--calibrate", type=float, help="Interactive calibration with KNOWN_MASS_KG (e.g., 2.000)")
    ap.add_argument("--samples", type=int, default=30, help="Samples to average/median during calibration")
    ap.add_argument("--timeout", type=float, default=6.0, help="Per-phase timeout (seconds) for calibration")
    ap.add_argument("--require-marker12", type=lambda x: int(x,0), default=None, help="Only accept frames where payload[12] == this byte (e.g., 0x0c)")
    ap.add_argument("--outlier-window", type=int, default=15, help="Window size for Hampel filter on raw readings")
    ap.add_argument("--nsigma", type=float, default=3.5, help="Sigma threshold for Hampel outlier rejection")

    args = ap.parse_args()

    if args.calibrate:
        if not args.mac:
            print("--mac is required for calibration mode.")
            return

        # Reuse previous calibration routine (omitted here for brevity)
        # Import at runtime to avoid duplication
        from rpi_ble_scanner import run_calibration  # type: ignore
        await run_calibration(args.adapter, args.mac, args.calibrate, samples=args.samples, timeout_s=args.timeout)
        return

    uuid_filter = None if args.uuid.lower() == "all" else args.uuid
    cb = make_callback(args.mac, uuid_filter, args.tare, args.scale, args.smooth, args.print_raw, args.require_marker12, args.outlier_window, args.nsigma)
    scanner = BleakScanner(detection_callback=cb, adapter=args.adapter, scanning_mode="active")
    await scanner.start()
    print(f"🔍 rpi_ble_scanner.py listening on {args.adapter}... (Ctrl+C to stop)")
    try:
        while True:
            await asyncio.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        await scanner.stop()

if __name__ == "__main__":
    asyncio.run(main())
