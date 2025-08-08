
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eddystone_sd_logger.py
Log raw Service Data bytes from E4BE adverts to a CSV for vendor analysis.

Columns include timestamp, RSSI, full hex payload, each byte b[0..N-1],
u16le windows [i..i+1], and convenience fields: battery_raw (b[3]), temp_raw_le (b[5:7], LE), temp_c, frame_id (b[16]).
"""

import argparse
import asyncio
from bleak import BleakScanner

DEFAULT_UUID = "0000e4be-0000-1000-8000-00805f9b34fb"

def bytes_to_hex(b: bytes) -> str:
    return b.hex()

def u16_pairs_le(b: bytes):
    out = []
    for i in range(max(0, len(b)-1)):
        out.append(int.from_bytes(b[i:i+2], "little", signed=False))
    return out

async def main():
    ap = argparse.ArgumentParser(description="Log E4BE Service Data adverts to CSV")
    ap.add_argument("--mac", required=True, help="Target MAC (case-insensitive)")
    ap.add_argument("--uuid", default=DEFAULT_UUID, help="Service UUID key")
    ap.add_argument("--seconds", type=int, default=20, help="How long to capture")
    ap.add_argument("--out", default="eddystone_sd_log.csv", help="CSV output path")
    args = ap.parse_args()

    mac_norm = args.mac.replace(":","").lower()
    rows = []

    def cb(device, adv):
        if device.address.replace(":","").lower() != mac_norm:
            return
        sd = (adv.service_data or {}).get(args.uuid)
        if not sd:
            for k,v in (adv.service_data or {}).items():
                if k.lower().endswith(args.uuid[-8:].lower()):
                    sd = v; break
        if not sd:
            return

        ts = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        rssi = getattr(adv, "rssi", None)
        hex_payload = sd.hex()
        b = list(sd)
        u16le = u16_pairs_le(sd)

        # convenience fields (best guesses)
        batt_raw = b[3] if len(b) > 3 else None
        temp_raw = int.from_bytes(sd[5:7], "little", signed=False) if len(b) >= 7 else None
        temp_c = (temp_raw / 10.0) if temp_raw is not None else None
        frame_id = b[16] if len(b) > 16 else None

        row = {
            "timestamp": ts,
            "mac": device.address,
            "rssi": rssi,
            "len": len(sd),
            "service_data_hex": hex_payload,
            "batt_raw": batt_raw,
            "temp_raw_le": temp_raw,
            "temp_c": temp_c,
            "frame_id": frame_id,
        }
        # add bytes
        for i, bi in enumerate(b):
            row[f"b[{i}]"] = bi
        # add u16le windows
        for i, v in enumerate(u16le):
            row[f"u16le[{i}]"] = v

        rows.append(row)

    async with BleakScanner(detection_callback=cb) as scanner:
        await asyncio.sleep(args.seconds)

    # Write CSV
    if rows:
        # stable superset of keys
        keys = sorted(set().union(*[r.keys() for r in rows]))
        with open(args.out, "w", newline="") as f:
            import csv
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"✅ Wrote {len(rows)} rows to {args.out}")
    else:
        print("⚠️ No rows captured; check MAC/UUID.")

if __name__ == "__main__":
    asyncio.run(main())
