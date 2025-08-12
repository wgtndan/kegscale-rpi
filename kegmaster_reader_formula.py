import asyncio
import argparse
from bleak import BleakScanner
from datetime import datetime

def parse_service_data(sd_hex):
    sd = bytes.fromhex(sd_hex)
    temp_c = sd[5] / 10
    seq = sd[9]
    raw24 = sd[13] | (sd[14] << 8) | (sd[15] << 16)
    status = sd[16] | (sd[17] << 8)
    weight_kg = (raw24 - 108801) * 0.00004296
    return temp_c, seq, raw24, status, weight_kg

def detection_callback(device, advertisement_data):
    for uuid, data in advertisement_data.service_data.items():
        if uuid.lower().startswith("0000e4be"):
            sd_hex = data.hex()
            temp_c, seq, raw24, status, weight_kg = parse_service_data(sd_hex)
            now = datetime.now().isoformat(timespec='seconds')
            print(f"{now} mac={device.address} rssi={device.rssi} "
                  f"kg={weight_kg:.3f} temp={temp_c:.1f}°C seq={seq} "
                  f"status=0x{status:04x} raw={raw24} sd={sd_hex}")

async def main(args):
    scanner = BleakScanner(detection_callback)
    await scanner.start()
    print("🔍 Listening for BLE advertisements... (Ctrl+C to stop)")
    try:
        while True:
            await asyncio.sleep(0.1)
    except KeyboardInterrupt:
        await scanner.stop()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mac", required=True, help="Target MAC address")
    args = parser.parse_args()
    asyncio.run(main(args))
