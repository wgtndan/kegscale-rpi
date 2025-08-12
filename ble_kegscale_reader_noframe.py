
#!/usr/bin/env python3
import argparse
import asyncio
import struct
from datetime import datetime
from bleak import BleakScanner
from collections import deque

def parse_args():
    parser = argparse.ArgumentParser(description="BLE Kegscale Reader - No frame filtering by default")
    parser.add_argument("--mac", required=True, help="MAC address of device")
    parser.add_argument("--raw-index", type=int, required=True, help="Index in service data for raw weight value (u16)")
    parser.add_argument("--endian", choices=["little", "big"], default="little", help="Endianness for weight bytes")
    parser.add_argument("--raw-shift", type=int, default=0, help="Bit shift to apply to raw value")
    parser.add_argument("--frame-index", type=int, default=-1, help="Frame byte index to filter (-1 = no filtering)")
    parser.add_argument("--frame-value", type=lambda x: int(x, 0), help="Frame value to match (e.g. 0xfe)")
    parser.add_argument("--raw-byte", choices=["none", "low", "high"], default="none", help="Select byte from u16 instead of full value")
    parser.add_argument("--batt-index", type=int, help="Index for battery byte")
    parser.add_argument("--temp-index", type=int, help="Index for temp (u16, deci-degC)")
    parser.add_argument("--smooth", type=int, default=1, help="Smoothing window size")
    parser.add_argument("--print-raw", action="store_true", help="Print raw service data")
    return parser.parse_args()

def extract_value(data, index, endian, raw_byte, shift):
    if index is None or index < 0 or index + 1 >= len(data):
        return None
    val = int.from_bytes(data[index:index+2], endian)
    if raw_byte == "low":
        val = val & 0xFF
    elif raw_byte == "high":
        val = (val >> 8) & 0xFF
    if shift != 0:
        val >>= shift
    return val

async def main():
    args = parse_args()
    history = deque(maxlen=args.smooth)

    def detection_callback(device, adv_data):
        if device.address.lower() != args.mac.lower():
            return
        for uuid, svc_data in adv_data.service_data.items():
            if args.print-raw:
                print(f"{device.address} {svc_data.hex()}")
            data = bytearray(svc_data)
            if args.frame_index >= 0 and args.frame_value is not None:
                if args.frame_index < len(data) and data[args.frame_index] != args.frame_value:
                    return
            raw_val = extract_value(data, args.raw_index, args.endian, args.raw_byte, args.raw_shift)
            if raw_val is not None:
                history.append(raw_val)
                avg_val = sum(history) / len(history)
            else:
                avg_val = None

            batt_val = data[args.batt_index] if args.batt_index is not None and args.batt_index < len(data) else None
            temp_val = None
            if args.temp_index is not None and args.temp_index + 1 < len(data):
                temp_val = int.from_bytes(data[args.temp_index:args.temp_index+2], args.endian) / 10

            ts = datetime.now().isoformat()
            print(f"{ts} raw={raw_val} avg={avg_val} temp={temp_val} batt={batt_val}")

    scanner = BleakScanner()
    scanner.register_detection_callback(detection_callback)
    await scanner.start()
    try:
        while True:
            await asyncio.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        await scanner.stop()

if __name__ == "__main__":
    asyncio.run(main())
