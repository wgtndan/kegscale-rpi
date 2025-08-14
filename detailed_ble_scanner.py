#!/usr/bin/env python3
"""
Detailed BLE scanner to find KegScale devices
"""

import asyncio
from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

class DetailedBLEScanner:
    def __init__(self):
        self.device_count = 0
        self.kegscale_found = False
        
    def detection_callback(self, device: BLEDevice, advertisement_data: AdvertisementData):
        """Callback for each advertisement detected."""
        self.device_count += 1
        
        # Print basic device info
        print(f"\n--- Device {self.device_count} ---")
        print(f"Name: {device.name}")
        print(f"Address: {device.address}")
        print(f"RSSI: {advertisement_data.rssi} dBm")
        
        # Check for service UUIDs
        if advertisement_data.service_uuids:
            print(f"Service UUIDs: {advertisement_data.service_uuids}")
            
            # Check for KegScale UUID
            target_uuid = "0000e4be-0000-1000-8000-00805f9b34fb"
            for uuid in advertisement_data.service_uuids:
                if str(uuid).lower() == target_uuid.lower():
                    print("*** KEGSCALE SERVICE UUID FOUND! ***")
                    self.kegscale_found = True
        
        # Check service data
        if advertisement_data.service_data:
            print("Service Data:")
            for uuid, data in advertisement_data.service_data.items():
                print(f"  {uuid}: {data.hex()}")
                
                # Check for KegScale service data
                if str(uuid).lower() == "0000e4be-0000-1000-8000-00805f9b34fb":
                    print("*** KEGSCALE SERVICE DATA FOUND! ***")
                    print(f"  Data length: {len(data)} bytes")
                    print(f"  Raw data: {data.hex()}")
                    self.kegscale_found = True
        
        # Check manufacturer data
        if advertisement_data.manufacturer_data:
            print("Manufacturer Data:")
            for company_id, data in advertisement_data.manufacturer_data.items():
                print(f"  Company 0x{company_id:04X}: {data.hex()}")
        
        # Limit output to prevent flooding
        if self.device_count >= 50:  # Stop after 50 devices to prevent spam
            print("\n[Limiting output to first 50 devices...]")
            return
            
    async def scan(self, duration=15):
        """Scan for devices."""
        print(f"Starting detailed BLE scan for {duration} seconds...")
        print("Looking for KegScale devices...\n")
        
        scanner = BleakScanner(detection_callback=self.detection_callback)
        
        try:
            await scanner.start()
            await asyncio.sleep(duration)
            await scanner.stop()
        except Exception as e:
            print(f"Error during scanning: {e}")
            
        print(f"\nScan completed!")
        print(f"Total advertisements: {self.device_count}")
        print(f"KegScale found: {'YES' if self.kegscale_found else 'NO'}")

async def main():
    scanner = DetailedBLEScanner()
    await scanner.scan(duration=20)

if __name__ == "__main__":
    asyncio.run(main())