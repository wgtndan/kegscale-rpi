import asyncio
import binascii
from beaconscanner import BeaconScanner
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore

# === Firebase Setup ===
cred = credentials.Certificate("serviceAccountKey.json")  # Make sure this file is in the same directory
firebase_admin.initialize_app(cred)
db = firestore.client()

# === Firebase Collections ===
success_collection = db.collection("ble_scans")
failure_collection = db.collection("ble_errors")

TARGET_TLM_UUID = "E4BE"  # Eddystone TLM frame UUID
TARGET_UID_UUID = "FEAA"  # Eddystone UID frame UUID

def process_packet(packet):
    try:
        # Check if the packet contains an Eddystone frame
        if packet.advertisement_data.get("Service UUIDs") == [TARGET_TLM_UUID, TARGET_UID_UUID]:
            # Decode TLM frame (E4BE)
            if TARGET_TLM_UUID in packet.advertisement_data.get("Service UUIDs"):
                # Extract battery, temperature, and other telemetry data
                tlm_data = decode_tlm(packet.payload)
                if tlm_data:
                    battery_level, temperature = tlm_data
                    peer_address = packet.addr
                    device_name = packet.get_name() or "Unknown"
                    rssi = packet.rssi
                    
                    # Log the data
                    hex_data = binascii.hexlify(packet.payload).decode().upper()
                    now = datetime.utcnow().isoformat() + "Z"
                    print(f"📡 {peer_address} | {device_name} | TLM Frame | Battery: {battery_level}% | Temp: {temperature}°C | RSSI: {rssi}")
                    
                    # Add to Firebase
                    doc = {
                        "timestamp": now,
                        "device_address": peer_address,
                        "device_name": device_name,
                        "battery_level": battery_level,
                        "temperature": temperature,
                        "rssi": rssi,
                        "service_data_raw": hex_data
                    }
                    success_collection.add(doc)

            # Decode UID frame (FEAA)
            if TARGET_UID_UUID in packet.advertisement_data.get("Service UUIDs"):
                uid_data = decode_uid(packet.payload)
                if uid_data:
                    namespace, instance = uid_data
                    peer_address = packet.addr
                    device_name = packet.get_name() or "Unknown"
                    rssi = packet.rssi
                    
                    # Log the data
                    hex_data = binascii.hexlify(packet.payload).decode().upper()
                    now = datetime.utcnow().isoformat() + "Z"
                    print(f"📡 {peer_address} | {device_name} | UID Frame | Namespace: {namespace} | Instance: {instance} | RSSI: {rssi}")
                    
                    # Add to Firebase
                    doc = {
                        "timestamp": now,
                        "device_address": peer_address,
                        "device_name": device_name,
                        "namespace": namespace,
                        "instance": instance,
                        "rssi": rssi,
                        "service_data_raw": hex_data
                    }
                    success_collection.add(doc)
        else:
            print("⚠️ Non-Eddystone packet detected.")
    except Exception as e:
        now = datetime.utcnow().isoformat() + "Z"
        print(f"⚠️ Error processing packet: {str(e)}")
        failure_collection.add({
            "timestamp": now,
            "error": str(e)
        })

def decode_tlm(payload):
    """Decode the Eddystone TLM frame"""
    # Assuming the TLM frame contains the battery and temperature at fixed positions
    if len(payload) >= 6:
        battery_level = payload[2]  # Byte 2 is battery level (as an example)
        temperature = (payload[3] << 8) + payload[4]  # Temperature data (just an example)
        return battery_level, temperature
    return None

def decode_uid(payload):
    """Decode the Eddystone UID frame"""
    # Extract the namespace and instance from the UID frame (example positions)
    if len(payload) >= 20:
        namespace = payload[2:10]  # Namespace is from byte 2 to 9
        instance = payload[10:18]  # Instance is from byte 10 to 17
        return namespace, instance
    return None

async def main():
    print("🔍 Listening for BLE advertisements...")
    
    # Create the BeaconScanner instance
    scanner = BeaconScanner(process_packet)

    # Start scanning for Eddystone frames
    scanner.start()

    try:
        while True:
            await asyncio.sleep(3600)  # Keep the service alive for 1 hour (or however long you need)
    except KeyboardInterrupt:
        print("🔚 Stopping scanner.")
        scanner.stop()  # Stop the scanner gracefully

if __name__ == "__main__":
    asyncio.run(main())
