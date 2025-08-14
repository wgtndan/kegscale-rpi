#!/usr/bin/env python3
"""
Monitor for ANY BLE service data to help debug KegScale detection
"""

import asyncio
from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

async def detection_callback(device: BLEDevice, advertisement_data: AdvertisementData):
    """Show only devices with service data."""
    
    # Only show devices that have service data
    if advertisement_data.service_data:
        print(f"\n--- Device with Service Data ---")
        print(f"Name: {device.name}")
        print(f"Address: {device.address}")
        print(f"RSSI: {advertisement_data.rssi} dBm")
        print("Service Data:")
        
        for uuid, data in advertisement_data.service_data.items():
            print(f"  UUID: {uuid}")
            print(f"  Data: {data.hex()}")
            print(f"  Length: {len(data)} bytes")
            
            # Check specifically for KegScale UUID
            if str(uuid).lower() == "0000e4be-0000-1000-8000-00805f9b34fb":
                print("  *** THIS IS THE KEGSCALE UUID! ***")

async def main():
    """Monitor for service data."""
    print("Monitoring for BLE service data...")
    print("Looking specifically for KegScale UUID: 0000e4be-0000-1000-8000-00805f9b34fb")
    print("Scanning for 30 seconds...\n")
    
    scanner = BleakScanner(detection_callback=detection_callback)
    
    try:
        await scanner.start()
        await asyncio.sleep(30)
        await scanner.stop()
    except Exception as e:
        print(f"Error: {e}")
    
    print("\nScan completed.")

if __name__ == "__main__":
    asyncio.run(main())