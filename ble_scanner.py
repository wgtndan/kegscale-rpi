import asyncio
import binascii
from bleak import BleakScanner
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
status_collection = db.collection("status_messages")

TARGET_TLM_UUID = "E4BE"  # Eddystone TLM frame UUID
TARGET_UID_UUID = "FEAA"  # Eddystone UID frame UUID

beacon_count = {}

def process_packet(device, advertisement_data):
    global beacon_count
    try:
        # Access service UUIDs directly
        service_uuids = advertisement_data.service_uuids
        
        if TARGET_TLM_UUID in service_uuids:
            # Process Eddystone TLM (Telemetry) frame
            tlm_data = decode_tlm(advertisement_data)
            if tlm_data:
                battery_level, temperature = tlm_data
                peer_address = device.address
                device_name = device.name or "Unknown"
                rssi = device.rssi
                
                # Log the data for TLM frame
                hex_data = binascii.hexlify(advertisement_data.get("raw_data")).decode().upper()
                now = datetime.utcnow().isoformat() + "Z"
                print(f"📡 {peer_address} | {device_name} | TLM Frame | Battery: {battery_level}% | Temp: {temperature}°C | RSSI: {rssi}")
                
                # Add data to Firebase
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

        elif TARGET_UID_UUID in service_uuids:
            # Process Eddystone UID frame
            uid_data = decode_uid(advertisement_data)
            if uid_data:
                namespace, instance = uid_data
                peer_address = device.address
                device_name = device.name or "Unknown"
                rssi = device.rssi
                
                # Log the data for UID frame
                hex_data = binascii.hexlify(advertisement_data.get("raw_data")).decode().upper()
                now = datetime.utcnow().isoformat() + "Z"
                print(f"📡 {peer_address} | {device_name} | UID Frame | Namespace: {namespace} | Instance: {instance} | RSSI: {rssi}")
                
                # Add data to Firebase
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

        # Increment beacon count by device address
        beacon_count[device.address] = beacon_count.get(device.address, 0) + 1

    except Exception as e:
        now = datetime.utcnow().isoformat() + "Z"
        print(f"⚠️ Error processing packet: {str(e)}")
        failure_collection.add({
            "timestamp": now,
            "error": str(e)
        })

def decode_tlm(advertisement_data):
    """Decode the Eddystone TLM frame"""
    # Access service data directly
    if TARGET_TLM_UUID in advertisement_data.service_data:
        service_data = advertisement_data.service_data[TARGET_TLM_UUID]
        if len(service_data) >= 6:
            battery_level = service_data[2]
            temperature = (service_data[3] << 8) + service_data[4]
            return battery_level, temperature
    return None

def decode_uid(advertisement_data):
    """Decode the Eddystone UID frame"""
    if TARGET_UID_UUID in advertisement_data.service_data:
        service_data = advertisement_data.service_data[TARGET_UID_UUID]
        if len(service_data) >= 20:
            namespace = service_data[2:10]
            instance = service_data[10:18]
            return namespace, instance
    return None

async def status_update():
    """Every 60 seconds, log the status with beacon count."""
    while True:
        try:
            now = datetime.utcnow().isoformat() + "Z"
            status_message = {
                "timestamp": now,
                "message": "Status update",
                "beacon_count": beacon_count
            }
            status_collection.add(status_message)
            print(f"📊 Status Update: {beacon_count}")
        except Exception as e:
            print(f"⚠️ Error logging status update: {str(e)}")
        await asyncio.sleep(60)

async def main():
    print("🔍 Listening for BLE advertisements...")
    
    # Create the BLE scanner using bleak (bleak 1.0.1)
    scanner = BleakScanner(detection_callback=process_packet)
    
    # Start scanning
    await scanner.start()
    
    # Run status update in the background
    asyncio.create_task(status_update())

    try:
        while True:
            await asyncio.sleep(3600)  # Keep the service alive for 1 hour (or however long you need)
    except KeyboardInterrupt:
        print("🔚 Stopping scanner.")
        await scanner.stop()  # Stop the scanner gracefully

if __name__ == "__main__":
    asyncio.run(main())
