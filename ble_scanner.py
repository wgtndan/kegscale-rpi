import asyncio
import binascii
from beaconscanner import BeaconScanner
from eddystone import EddystoneTLMFrame, EddystoneUIDFrame, EddystoneFilter
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
        # Filter out only Eddystone frames
        if EddystoneFilter.filter(packet):
            # Check if it's an Eddystone TLM frame
            if packet.get_type() == "EddystoneTLM":
                tlm_frame = EddystoneTLMFrame(packet)
                if tlm_frame.is_valid():
                    # Extract data from the TLM frame
                    peer_address = packet.addr
                    device_name = packet.get_name() or "Unknown"
                    battery_level = tlm_frame.battery
                    temperature = tlm_frame.temperature
                    rssi = packet.rssi
                    
                    # Print out the data for logging
                    hex_data = binascii.hexlify(packet.payload).decode().upper()
                    now = datetime.utcnow().isoformat() + "Z"
                    print(f"📡 {peer_address} | {device_name} | TLM Frame | Battery: {battery_level}% | Temp: {temperature}°C | RSSI: {rssi}")
                    
                    # Add the data to Firebase
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
                else:
                    print("⚠️ Invalid TLM frame received.")
            
            # Check if it's an Eddystone UID frame with UUID FEAA
            elif packet.get_type() == "EddystoneUID":
                uid_frame = EddystoneUIDFrame(packet)
                if uid_frame.is_valid():
                    # Extract data from the UID frame
                    peer_address = packet.addr
                    device_name = packet.get_name() or "Unknown"
                    namespace = uid_frame.namespace
                    instance = uid_frame.instance
                    rssi = packet.rssi
                    
                    # Print out the data for logging
                    hex_data = binascii.hexlify(packet.payload).decode().upper()
                    now = datetime.utcnow().isoformat() + "Z"
                    print(f"📡 {peer_address} | {device_name} | UID Frame | Namespace: {namespace} | Instance: {instance} | RSSI: {rssi}")
                    
                    # Add the data to Firebase
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
                    print("⚠️ Invalid UID frame received.")
        else:
            print("⚠️ Non-Eddystone packet detected.")
    except Exception as e:
        now = datetime.utcnow().isoformat() + "Z"
        print(f"⚠️ Error processing packet: {str(e)}")
        failure_collection.add({
            "timestamp": now,
            "error": str(e)
        })

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
