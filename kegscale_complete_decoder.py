#!/usr/bin/env python3
"""
KegScale Complete Decoder - Standalone Version
Decodes KegScale BLE beacon data without requiring additional libraries.
Based on logic extracted from the KegMaster Android app.
"""

import struct
import json
from datetime import datetime


class KegScaleDecoder:
    def __init__(self):
        self.battery_voltage_table = self._create_battery_table()
    
    def _create_battery_table(self):
        """
        Battery voltage to percentage lookup table from KegMaster app.
        Maps millivolt readings to battery percentage (0-100%).
        """
        voltages = [
            3165, 3246, 3293, 3327, 3353, 3374, 3392, 3408, 3422, 3434,  # 0-9%
            3445, 3455, 3465, 3473, 3481, 3489, 3496, 3502, 3506, 3514,  # 10-19%
            3522, 3531, 3539, 3547, 3555, 3563, 3571, 3580, 3588, 3596,  # 20-29%
            3604, 3612, 3620, 3629, 3637, 3645, 3653, 3661, 3669, 3678,  # 30-39%
            3686, 3694, 3702, 3710, 3718, 3727, 3735, 3743, 3751, 3759,  # 40-49%
            3767, 3776, 3784, 3792, 3800, 3808, 3817, 3825, 3833, 3841,  # 50-59%
            3849, 3857, 3866, 3874, 3882, 3890, 3898, 3906, 3915, 3923,  # 60-69%
            3931, 3939, 3947, 3955, 3964, 3972, 3980, 3988, 3996, 4004,  # 70-79%
            4013, 4021, 4029, 4037, 4045, 4054, 4062, 4070, 4078, 4086,  # 80-89%
            4094, 4103, 4111, 4119, 4127, 4135, 4143, 4152, 4160, 4168   # 90-100%
        ]
        return voltages
    
    def mv_to_battery_percentage(self, millivolts):
        """
        Convert millivolt reading to battery percentage.
        Uses the exact lookup table from the KegMaster app.
        """
        if millivolts < 3165:
            return 0
        
        for i, voltage in enumerate(self.battery_voltage_table):
            if millivolts < voltage:
                return i
        
        return 100
    
    def celsius_to_fahrenheit(self, celsius, round_digits=True, decimal_places=1):
        """
        Convert Celsius to Fahrenheit using KegMaster app formula.
        Formula: F = (C * 9/5) + 32
        """
        fahrenheit = (9 * celsius / 5) + 32
        if round_digits:
            return round(fahrenheit, decimal_places)
        return fahrenheit
    
    def fahrenheit_to_celsius(self, fahrenheit, round_digits=True, decimal_places=1):
        """
        Convert Fahrenheit to Celsius using KegMaster app formula.
        Formula: C = (F - 32) * 5/9
        """
        celsius = (fahrenheit - 32) * 5 / 9
        if round_digits:
            return round(celsius, decimal_places)
        return celsius
    
    def grams_to_pounds(self, grams):
        """Convert grams to pounds."""
        return grams * 0.00220462
    
    def grams_to_kg(self, grams):
        """Convert grams to kilograms."""
        return grams / 1000.0
    
    def decode_kegscale_beacon(self, payload_hex):
        """
        Decode KegScale BLE beacon payload from hex string.
        
        Args:
            payload_hex: Hex string of the beacon payload
            
        Returns:
            Dictionary with decoded values
        """
        decoded = {
            "timestamp": datetime.now().isoformat(),
            "raw_payload": payload_hex,
            "payload_length": len(payload_hex) // 2
        }
        
        try:
            # Convert hex string to bytes
            if isinstance(payload_hex, str):
                payload = bytes.fromhex(payload_hex.replace(" ", ""))
            else:
                payload = payload_hex
            
            if len(payload) < 17:
                decoded["error"] = "Payload too short for weight data"
                return decoded
            
            # Extract weight (bytes 13-17, little endian, signed 32-bit)
            weight_raw = struct.unpack('<i', payload[13:17])[0]
            decoded["weight_raw"] = weight_raw
            decoded["weight_grams"] = weight_raw
            decoded["weight_kg"] = self.grams_to_kg(weight_raw)
            decoded["weight_pounds"] = self.grams_to_pounds(weight_raw)
            
            # Extract battery voltage (bytes 17-19, little endian, unsigned 16-bit)
            if len(payload) >= 19:
                battery_raw = struct.unpack('<H', payload[17:19])[0]
                decoded["battery_raw"] = battery_raw
                decoded["battery_mv"] = battery_raw
                decoded["battery_percentage"] = self.mv_to_battery_percentage(battery_raw)
            
            # Extract temperature (bytes 19-21, little endian, signed 16-bit)
            if len(payload) >= 21:
                temp_raw = struct.unpack('<h', payload[19:21])[0]
                decoded["temperature_raw"] = temp_raw
                # Temperature is likely in centidegrees Celsius (1/100th of a degree)
                temp_celsius = temp_raw / 100.0
                decoded["temperature_celsius"] = temp_celsius
                decoded["temperature_fahrenheit"] = self.celsius_to_fahrenheit(temp_celsius)
            
            # Additional metadata
            if len(payload) >= 13:
                decoded["device_info_hex"] = payload[0:13].hex()
            
            decoded["success"] = True
            
        except Exception as e:
            decoded["error"] = f"Decoding error: {str(e)}"
            decoded["success"] = False
        
        return decoded
    
    def decode_from_scanrecord(self, scanrecord_hex):
        """
        Decode from a full scan record hex string.
        Extracts manufacturer data and service data for decoding.
        """
        try:
            scanrecord = bytes.fromhex(scanrecord_hex.replace(" ", ""))
            
            # Parse scan record for manufacturer data
            # Manufacturer data typically starts with AD type 0xFF
            i = 0
            results = []
            
            while i < len(scanrecord):
                if i + 1 >= len(scanrecord):
                    break
                    
                length = scanrecord[i]
                if length == 0 or i + length + 1 > len(scanrecord):
                    break
                
                ad_type = scanrecord[i + 1]
                ad_data = scanrecord[i + 2:i + 1 + length]
                
                if ad_type == 0xFF and len(ad_data) >= 2:  # Manufacturer data
                    company_id = struct.unpack('<H', ad_data[0:2])[0]
                    payload = ad_data[2:]
                    
                    print(f"\nFound manufacturer data for company 0x{company_id:04X}")
                    decoded = self.decode_kegscale_beacon(payload)
                    results.append(decoded)
                
                i += length + 1
            
            return results
            
        except Exception as e:
            return [{"error": f"Scan record parsing error: {str(e)}"}]


def main():
    """
    Main function for testing the decoder.
    """
    decoder = KegScaleDecoder()
    
    print("KegScale Complete Decoder - Test Mode")
    print("=" * 50)
    
    # Test battery conversion
    print("\nBattery Conversion Test:")
    test_voltages = [3000, 3165, 3500, 3800, 4000, 4200]
    for mv in test_voltages:
        percentage = decoder.mv_to_battery_percentage(mv)
        print(f"  {mv}mV -> {percentage}%")
    
    # Test temperature conversion
    print("\nTemperature Conversion Test:")
    test_temps = [0, 20, 25, 30, 100]
    for c in test_temps:
        f = decoder.celsius_to_fahrenheit(c)
        print(f"  {c}°C -> {f}°F")
    
    # Test with sample data (replace with your actual beacon hex data)
    print("\nSample Beacon Decoding:")
    sample_hex = "0123456789ABCDEF" + "12345678" + "9ABC" + "CDEF"  # Replace with real data
    if len(sample_hex) >= 34:  # Ensure minimum length
        result = decoder.decode_kegscale_beacon(sample_hex)
        print(json.dumps(result, indent=2))
    else:
        print("No sample data provided - replace sample_hex with actual beacon data")
    
    print("\nTo use with real data:")
    print("1. Run your existing BLE scanner to capture beacon hex data")
    print("2. Call decoder.decode_kegscale_beacon(hex_string)")
    print("3. Or use decoder.decode_from_scanrecord(full_scanrecord_hex)")


if __name__ == "__main__":
    main()