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
        # Fuzzy match for MAC address - looks for devices starting with "5F:84"
        # if device.address.upper().startswith("5F"):
        now = datetime.utcnow().isoformat() + "Z"
        
        # Collect all available data
        data = {
            "timestamp": now,
            "address": device.address or "Unknown",
            "name": device.name or "Unknown",
            # "rssi": device.rssi,
            "service_uuids": str(advertisement_data.service_uuids) or [''],
            "service_data": str({k: v.hex() for k, v in advertisement_data.service_data.items()}) or {'nothing': 'N/A'},
            "manufacturer_data": str({k: v.hex() for k, v in advertisement_data.manufacturer_data.items()}) or {'nothing': 'N/A'},
            "local_name": advertisement_data.local_name or "N/A",
            # "tx_power": advertisement_data.tx_power,
        }

        if data['address'] in ["7A:29:98:88:00:FC", "61:F8:54:E2:47:19", "6C:D5:33:F7:94:04"]:
            return
        
        # # Print detailed info
        # print("\n🔍 Device Found:")
        # print(f"📱 Address: {data['address']}")
        # print(f"📛 Name: {data['name']}")
        # # print(f"📶 RSSI: {data['rssi']}dBm")
        # if data['service_uuids']:
        #     print(f"🔧 Service UUIDs: {data['service_uuids']}")
        # if data['service_data']:
        #     print(f"📄 Service Data: {data['service_data']}")
        # if data['manufacturer_data']:
        #     print(f"🏭 Manufacturer Data: {data['manufacturer_data']}")
        # if data['local_name']:
        #     print(f"✏️ Local Name: {data['local_name']}")
        # # if data['tx_power'] is not None:
        # #     print(f"📡 TX Power: {data['tx_power']}dBm")
        # print("-" * 50)
        
        # Save to Firebase
        success_collection.add(data)
            
        # Update beacon count
        beacon_count[device.address] = beacon_count.get(device.address, 0) + 1

    except Exception as e:
        now = datetime.utcnow().isoformat() + "Z"
        print(f"⚠️ Error processing packet: {str(e)}")
        failure_collection.add({
            "timestamp": now,
            "error": str(e),
            "data": str(data)
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
    """Every 60 seconds, log the status with beacon count if any beacons were detected."""
    while True:
        try:
            print(f"📊 Checking Status...")
            if beacon_count:  # Only proceed if beacon_count is not empty
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
        await asyncio.sleep(10)

async def main():
    print("🔍 Listening for BLE advertisements...")
    
    # Create the BLE scanner using bleak (bleak 1.0.1)
    scanner = BleakScanner(detection_callback=process_packet)
    
    # Start scanning
    await scanner.start()
    
    try:
        # Create and run the status update task indefinitely
        status_task = asyncio.create_task(status_update())
        # Wait forever or until interrupted
        await status_task
    except KeyboardInterrupt:
        print("🔚 Stopping scanner.")
        await scanner.stop()  # Stop the scanner gracefully

if __name__ == "__main__":
    asyncio.run(main())
