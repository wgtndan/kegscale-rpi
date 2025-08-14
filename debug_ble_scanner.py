#!/usr/bin/env python3
"""
Debug BLE scanner to see all devices and their service UUIDs
"""

import asyncio
from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

class DebugBLEScanner:
    def __init__(self):
        self.scan_count = 0
        self.devices_seen = set()
    
    def detection_callback(self, device: BLEDevice, advertisement_data: AdvertisementData):
        """Debug callback to show all devices."""
        if device.address in self.devices_seen:
            return  # Skip duplicates for cleaner output
            
        self.devices_seen.add(device.address)
        self.scan_count += 1
        
        print(f"\n--- Device #{self.scan_count} ---")
        print(f"Name: {device.name}")
        print(f"Address: {device.address}")
        print(f"RSSI: {advertisement_data.rssi} dBm")
        
        # Show service UUIDs
        if advertisement_data.service_uuids:
            print(f"Service UUIDs: {advertisement_data.service_uuids}")
        
        # Show service data
        if advertisement_data.service_data:
            print("Service Data:")
            for uuid, data in advertisement_data.service_data.items():
                print(f"  {uuid}: {data.hex()}")
                if str(uuid).lower() == "0000e4be-0000-1000-8000-00805f9b34fb":
                    print(f"  *** KEGSCALE SERVICE FOUND! ***")
        
        # Show manufacturer data
        if advertisement_data.manufacturer_data:
            print("Manufacturer Data:")
            for company_id, data in advertisement_data.manufacturer_data.items():
                print(f"  Company 0x{company_id:04X}: {data.hex()}")
    
    async def scan(self, duration=10):
        """Scan for BLE devices."""
        print(f"Starting debug BLE scan for {duration} seconds...")
        print("Looking for all BLE devices...")
        
        scanner = BleakScanner(detection_callback=self.detection_callback)
        
        try:
            await scanner.start()
            await asyncio.sleep(duration)
            await scanner.stop()
        except KeyboardInterrupt:
            print("\nScan interrupted by user")
            await scanner.stop()
        
        print(f"\nScan completed. Unique devices detected: {self.scan_count}")

async def main():
    """Run debug scanner."""
    scanner = DebugBLEScanner()
    await scanner.scan(duration=15)

if __name__ == "__main__":
    print("Debug BLE Scanner")
    print("=" * 30)
    asyncio.run(main())