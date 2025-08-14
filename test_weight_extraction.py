#!/usr/bin/env python3
"""
Test different weight extraction positions from KegScale beacon data.
"""

import struct

def test_weight_positions(hex_payload):
    """Test different byte positions for weight extraction."""
    payload = bytes.fromhex(hex_payload.replace(" ", ""))
    print(f"Payload: {hex_payload}")
    print(f"Length: {len(payload)} bytes")
    print()
    
    # Test different positions for 32-bit values
    positions_to_test = [
        (0, 4, "bytes 0-4"),
        (4, 8, "bytes 4-8"),
        (8, 12, "bytes 8-12"),
        (9, 13, "bytes 9-13"),
        (10, 14, "bytes 10-14"),
        (11, 15, "bytes 11-15"),
        (12, 16, "bytes 12-16"),
        (13, 17, "bytes 13-17 (current)"),
    ]
    
    print("32-bit signed little-endian extractions:")
    for start, end, desc in positions_to_test:
        if end <= len(payload):
            try:
                value = struct.unpack('<i', payload[start:end])[0]
                print(f"  {desc}: {value:,}")
            except:
                print(f"  {desc}: ERROR")
    
    print("\n16-bit signed little-endian extractions:")
    for i in range(0, len(payload)-1, 2):
        try:
            value = struct.unpack('<h', payload[i:i+2])[0]
            print(f"  bytes {i}-{i+2}: {value}")
        except:
            pass
    
    print("\nByte-by-byte (hex and decimal):")
    for i, byte in enumerate(payload):
        print(f"  byte {i}: 0x{byte:02X} ({byte})")

# Test with your sample data
print("=== Empty Scale Data ===")
test_weight_positions("20000eca02f800002a5200104d5801fffa")

print("\n=== Phone on Scale Data ===") 
test_weight_positions("20000eca02f800002a7400105aeb010041")