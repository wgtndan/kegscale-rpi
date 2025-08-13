#!/usr/bin/env python3
"""
KegScale BLE Beacon Decoder
Decodes KegScale BLE beacon data for weight, battery percentage, and temperature.
Based on logic extracted from the KegMaster Android app.
"""

import asyncio
import json
import logging
from datetime import datetime
from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class KegScaleDecoder:
    def __init__(self):
        self.battery_voltage_table = self._create_battery_table()
    
    def _create_battery_table(self):
        """
        Battery voltage to percentage lookup table from KegMaster app.
        Maps millivolt readings to battery percentage.
        """
        table = []
        voltages = [
            3165, 3246, 3293, 3327, 3353, 3374, 3392, 3408, 3422, 3434,
            3445, 3455, 3465, 3473, 3481, 3489, 3496, 3502, 3506, 3514,
            3522, 3531, 3539, 3547, 3555, 3563, 3571, 3580, 3588, 3596,
            3604, 3612, 3620, 3629, 3637, 3645, 3653, 3661, 3669, 3678,
            3686, 3694, 3702, 3710, 3718, 3727, 3735, 3743, 3751, 3759,
            3767, 3776, 3784, 3792, 3800, 3808, 3817, 3825, 3833, 3841,
            3849, 3857, 3866, 3874, 3882, 3890, 3898, 3906, 3915, 3923,
            3931, 3939, 3947, 3955, 3964, 3972, 3980, 3988, 3996, 4004,
            4013, 4021, 4029, 4037, 4045, 4054, 4062, 4070, 4078, 4086,
            4094, 4103, 4111, 4119, 4127, 4135, 4143, 4152, 4160, 4168
        ]
        
        for i, voltage in enumerate(voltages):
            table.append((voltage, i))
        
        return table
    
    def mv_to_battery_percentage(self, millivolts):
        """
        Convert millivolt reading to battery percentage.
        Uses the exact lookup table from the KegMaster app.
        """
        if millivolts < 3165:
            return 0
        
        for voltage, percentage in self.battery_voltage_table:
            if millivolts < voltage:
                return percentage
        
        return 100
    
    def celsius_to_fahrenheit(self, celsius, round_digits=True, decimal_places=1):
        """
        Convert Celsius to Fahrenheit using KegMaster app formula.
        """
        fahrenheit = (9 * celsius / 5) + 32
        if round_digits:
            return round(fahrenheit, decimal_places)
        return fahrenheit
    
    def fahrenheit_to_celsius(self, fahrenheit, round_digits=True, decimal_places=1):
        """
        Convert Fahrenheit to Celsius using KegMaster app formula.
        """
        celsius = (fahrenheit - 32) * 5 / 9
        if round_digits:
            return round(celsius, decimal_places)
        return celsius
    
    def calculate_weight_remaining(self, start_total_weight, start_gas_weight, current_weight):
        """
        Calculate remaining weight using KegMaster app logic.
        """
        weight_consumed = start_total_weight - current_weight
        remaining = start_gas_weight - weight_consumed
        return max(0, min(remaining, start_gas_weight))
    
    def decode_kegscale_beacon(self, payload):
        """
        Decode KegScale BLE beacon payload.
        
        Based on analysis of KegMaster app and your existing Python decoder.
        Extracts weight, battery, and temperature from the beacon data.
        """
        decoded = {
            "timestamp": datetime.now().isoformat(),
            "raw_payload": payload.hex(),
            "payload_length": len(payload)
        }
        
        if len(payload) < 17:
            decoded["error"] = "Payload too short"
            return decoded
        
        try:
            # Extract raw weight (bytes 13-17, little endian, signed)
            weight_raw = int.from_bytes(payload[13:17], "little", signed=True)
            decoded["weight_raw"] = weight_raw
            decoded["weight_grams"] = weight_raw  # Assuming grams
            decoded["weight_kg"] = weight_raw / 1000.0
            decoded["weight_pounds"] = weight_raw * 0.00220462
            
            # Extract battery voltage (bytes 17-19, little endian)
            if len(payload) >= 19:
                battery_raw = int.from_bytes(payload[17:19], "little", signed=False)
                decoded["battery_raw"] = battery_raw
                decoded["battery_mv"] = battery_raw
                decoded["battery_percentage"] = self.mv_to_battery_percentage(battery_raw)
            
            # Extract temperature (bytes 19-21, little endian, signed)
            if len(payload) >= 21:
                temp_raw = int.from_bytes(payload[19:21], "little", signed=True)
                decoded["temperature_raw"] = temp_raw
                # Temperature is likely in centidegrees Celsius (1/100th of a degree)
                temp_celsius = temp_raw / 100.0
                decoded["temperature_celsius"] = temp_celsius
                decoded["temperature_fahrenheit"] = self.celsius_to_fahrenheit(temp_celsius)
            
            # Additional fields that might be present
            if len(payload) >= 13:
                decoded["device_info"] = payload[0:13].hex()
            
        except Exception as e:
            decoded["error"] = f"Decoding error: {str(e)}"
        
        return decoded


class KegScaleBLEScanner:
    def __init__(self, device_filter=None):
        self.decoder = KegScaleDecoder()
        self.device_filter = device_filter
        self.scan_count = 0
    
    def detection_callback(self, device: BLEDevice, advertisement_data: AdvertisementData):
        """
        Callback function for BLE advertisement detection.
        """
        self.scan_count += 1
        
        # Filter devices if specified
        if self.device_filter:
            device_name = device.name or ""
            device_address = device.address or ""
            if (self.device_filter.lower() not in device_name.lower() and 
                self.device_filter.lower() not in device_address.lower()):
                return
        
        # Look for manufacturer data or service data
        manufacturer_data = advertisement_data.manufacturer_data
        service_data = advertisement_data.service_data
        
        print(f"\n--- Scan #{self.scan_count} ---")
        print(f"Device: {device.name} ({device.address})")
        print(f"RSSI: {advertisement_data.rssi} dBm")
        
        # Process manufacturer data
        for company_id, data in manufacturer_data.items():
            print(f"Manufacturer ID: 0x{company_id:04X}")
            decoded = self.decoder.decode_kegscale_beacon(data)
            print("Decoded Data:")
            print(json.dumps(decoded, indent=2))
        
        # Process service data
        for service_uuid, data in service_data.items():
            print(f"Service UUID: {service_uuid}")
            decoded = self.decoder.decode_kegscale_beacon(data)
            print("Service Data Decoded:")
            print(json.dumps(decoded, indent=2))
        
        # Also show raw advertisement data
        if hasattr(advertisement_data, 'manufacturer_data') or hasattr(advertisement_data, 'service_data'):
            print(f"Raw adv data: {advertisement_data}")
    
    async def scan(self, duration=30):
        """
        Scan for BLE devices and decode KegScale beacons.
        """
        print(f"Starting KegScale BLE scan for {duration} seconds...")
        print("Looking for KegScale beacon data...")
        
        scanner = BleakScanner(detection_callback=self.detection_callback)
        
        try:
            await scanner.start()
            await asyncio.sleep(duration)
            await scanner.stop()
        except KeyboardInterrupt:
            print("\nScan interrupted by user")
            await scanner.stop()
        
        print(f"\nScan completed. Total devices detected: {self.scan_count}")


async def main():
    """
    Main function to run the KegScale BLE scanner.
    """
    # Target your specific KegScale device
    target_mac = "5C:01:3B:35:92:EE"
    scanner = KegScaleBLEScanner(device_filter=target_mac)
    await scanner.scan(duration=60)  # Scan for 60 seconds


if __name__ == "__main__":
    print("KegScale BLE Beacon Decoder")
    print("=" * 40)
    asyncio.run(main())