import argparse
import asyncio
import struct
from bleak import BleakScanner
from datetime import datetime

UUID_E4BE = "0000e4be-0000-1000-8000-00805f9b34fb"

# Your given tare value
TARE_VALUE = 118_295
SCALING_FACTOR = 0.000000045885

def parse_service_data(sd_hex):
    sd = bytes.fromhex(sd_hex)
    if len(sd) < 17:
        return None

    # Bytes 13,14,15,16 (indexes 12,13,14,15 in 0-based indexing) as 32-bit signed LE
    raw32 = struct.unpack("<i", sd[12:16])[0]

    # Calculate weight in kg
    weight_kg = (TARE_VALUE - raw32) * SCALING_FACTOR
    return raw32, weight_kg

def detection_callback(device, advertisement_data):
    if args.mac and device.address.lower() != args.mac.lower():
        return

    service_data = advertisement_data.service_data
    if UUID_E4BE not in service_data:
        return

    sd_hex = service_data[UUID_E4BE].hex()
    parsed = parse_service_data(sd_hex)
    if parsed:
        raw32, weight_kg = parsed
        print(f"{datetime.now().isoformat()} raw32={raw32} weight={weight_kg:.3f} kg sd={sd_hex}")

async def main():
    scanner = BleakScanner()
    scanner.register_detection_callback(detection_callback)
    await scanner.start()
    await asyncio.sleep(args.seconds)
    await scanner.stop()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mac", help="MAC address filter")
    parser.add_argument("--seconds", type=int, default=60, help="Scan time in seconds")
    args = parser.parse_args()

    asyncio.run(main())
