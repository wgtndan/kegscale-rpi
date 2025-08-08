#!/usr/bin/env python3
import asyncio
import argparse
import binascii
import json
import sys
from datetime import datetime, timezone

from bleak import BleakScanner

# 16-bit Service UUID we're targeting
SERVICE_UUID_16 = 0xE4BE
# Convert to the 128-bit UUID string representation used by BlueZ/Bleak for 16-bit service data
SERVICE_UUID_128 = f"0000{SERVICE_UUID_16:04x}-0000-1000-8000-00805f9b34fb"


def decode_service_data(payload: bytes) -> dict:
    """
    Best-current-knowledge decoder for the custom service data (UUID 0xE4BE).

    Layout hypothesis (indexing starts at 0, *after* the 2-byte UUID that nRF Connect already strips):
      [0]   flags/status?         (u8)      -> left as raw
      [1]   sequence / frame id?  (u8)      -> left as raw
      [2]   battery_raw           (u8)      -> mapped ~0-100% (see below)
      [3:5] unknown_1             (u16 LE)  -> left as raw
      [5:7] temperature_ddec_c    (u16 LE)  -> Temperature in deci-degC (e.g., 233 -> 23.3°C)
      [7:8] reserved?             (u8)      -> left as raw
      [8:10] weight_g             (u16 LE)  -> Weight in grams (hypothesis)
      [10:12] unknown_2           (u16 LE)
      [12:14] unknown_3           (u16 LE)
      [14:16] unknown_4           (u16 LE)
      [16]   status_2?            (u8)

    NOTE: This is intentionally lenient. If the payload is shorter, fields will be missing.
    """
    out = {"raw_hex": payload.hex()}

    def le16(b: bytes, start: int):
        if len(b) >= start + 2:
            return int.from_bytes(b[start:start+2], "little")
        return None

    def u8(b: bytes, idx: int):
        if len(b) > idx:
            return b[idx]
        return None

    # raw bytes for debugging/telemetry
    out["len"] = len(payload)

    out["flags"] = u8(payload, 0)
    out["frame"] = u8(payload, 1)

    batt_raw = u8(payload, 2)
    out["battery_raw"] = batt_raw
    if batt_raw is not None:
        # Working guess: 0..21 ~ 0..100% (based on 0x0F ≈ 70%). Clamp 0..21 to avoid >100.
        out["battery_pct_guess"] = round(min(batt_raw, 21) / 21 * 100)

    out["unknown_1"] = le16(payload, 3)

    temp_ddec = le16(payload, 5)
    out["temperature_deci_c"] = temp_ddec
    if temp_ddec is not None:
        out["temperature_c"] = round(temp_ddec / 10.0, 1)

    out["reserved_0x7"] = u8(payload, 7)

    weight_g = le16(payload, 8)
    out["weight_g"] = weight_g
    if weight_g is not None:
        out["weight_kg"] = round(weight_g / 1000.0, 3)
        out["weight_lb"] = round(weight_g * 0.00220462, 3)

    out["unknown_2"] = le16(payload, 10)
    out["unknown_3"] = le16(payload, 12)
    out["unknown_4"] = le16(payload, 14)
    out["status_2"] = u8(payload, 16)

    return out


async def run(adapter: str, duration: float, once: bool, csv_path: str | None):
    # Prepare CSV if requested
    csv_file = None
    if csv_path:
        import csv
        csv_file = open(csv_path, "a", newline="")
        csv_writer = csv.writer(csv_file)
        # header (only if file is empty)
        if csv_file.tell() == 0:
            csv_writer.writerow([
                "ts_iso", "mac", "rssi", "raw_hex",
                "battery_pct_guess", "temperature_c",
                "weight_g", "weight_kg", "weight_lb"
            ])

    # Event to stop when --once is used
    stop_event = asyncio.Event()

    def detection_callback(device, advertisement_data):
        # Filter only packets that include our Service Data UUID
        sd = advertisement_data.service_data or {}
        payload = sd.get(SERVICE_UUID_128)
        if not payload:
            return

        ts = datetime.now(timezone.utc).isoformat()
        decoded = decode_service_data(payload)

        # Prefer RSSI from advertisement_data; fall back to device.metadata if missing
        rssi = getattr(advertisement_data, "rssi", None)
        rssi_source = "adv"
        if rssi is None:
            rssi = (getattr(device, "metadata", {}) or {}).get("rssi")
            rssi_source = "device.metadata" if rssi is not None else "unknown"

        record = {
            "ts_iso": ts,
            "mac": device.address,
            "rssi": rssi,
            "rssi_source": rssi_source,
            "uuid": SERVICE_UUID_128,
            **decoded,
        }

        # Print newline-delimited JSON (friendly for `jq` and log shippers)
        print(json.dumps(record, ensure_ascii=False), flush=True)

        # Optionally append to CSV
        if csv_path:
            csv_writer.writerow([
                ts,
                device.address,
                advertisement_data.rssi,
                decoded.get("raw_hex"),
                decoded.get("battery_pct_guess"),
                decoded.get("temperature_c"),
                decoded.get("weight_g"),
                decoded.get("weight_kg"),
                decoded.get("weight_lb"),
            ])
            csv_file.flush()

        if once:
            stop_event.set()

    scanner = BleakScanner(detection_callback, adapter=adapter)

    await scanner.start()
    try:
        if once:
            await stop_event.wait()
        else:
            await asyncio.sleep(duration)
    finally:
        await scanner.stop()
        if csv_file:
            csv_file.close()


def main():
    parser = argparse.ArgumentParser(
        description="Scan BLE advertisements for Service Data 0xE4BE and decode payloads."
    )
    parser.add_argument("--hci", dest="adapter", default="hci0", help="HCI adapter (default: hci0)")
    parser.add_argument("--duration", type=float, default=60.0, help="How long to run in seconds (ignored with --once)")
    parser.add_argument("--once", action="store_true", help="Stop after first matching packet")
    parser.add_argument("--csv", dest="csv_path", default=None, help="Append decoded rows to this CSV file")

    args = parser.parse_args()

    try:
        asyncio.run(run(args.adapter, args.duration, args.once, args.csv_path))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
