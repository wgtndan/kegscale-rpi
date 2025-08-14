#!/usr/bin/env python3
"""
Simple BLE test to see if we can detect any devices at all
"""

import asyncio
from bleak import BleakScanner

async def main():
    print("Starting simple BLE scan...")
    
    try:
        # Simple scan for 10 seconds
        devices = await BleakScanner.discover(timeout=10.0)
        
        print(f"Found {len(devices)} devices:")
        for i, device in enumerate(devices):
            print(f"{i+1}. {device.name} ({device.address}) - RSSI: {device.rssi}")
            
    except Exception as e:
        print(f"Error during scan: {e}")

if __name__ == "__main__":
    asyncio.run(main())