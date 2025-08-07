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
        now = datetime.utcnow().isoformat() + "Z"
        
        # Collect all available data
        data = {
            "timestamp": now,
            "address": device.address or "Unknown",
            "name": device.name or "Unknown",
            "rssi": advertisement_data.rssi,
            "service_uuids": str(advertisement_data.service_uuids) or [''],
            "service_data": str({k: v.hex() for k, v in advertisement_data.service_data.items()}) or {'nothing': 'N/A'},
            "manufacturer_data": str({k: v.hex() for k, v in advertisement_data.manufacturer_data.items()}) or {'nothing': 'N/A'},
            "local_name": advertisement_data.local_name or "N/A",
            "tx_power": advertisement_data.tx_power,
            "device": str(device),
            "advertisement": str(advertisement_data) or "N/A"
        }

        if data['address'] in ["7A:29:98:88:00:FC", "61:F8:54:E2:47:19", "6C:D5:33:F7:94:04""57:73:80:35:F5:47", "63:AB:6B:B1:FE:5E", "50:51:A9:FE:69:38", "22:EA:0E:25:A7:12", "68:72:C3:83:55:4B", "E7:E7:F4:A0:F8:54", "63:DE:C2:3F:88:F6", "2C:B4:3A:02:98:86", "51:21:6A:3F:29:A0", "62:46:31:9B:28:E1", "7B:42:DB:C2:DF:BF", "65:5A:EC:00:0A:33", "60:D2:38:B5:EC:EC", "6F:7D:30:E3:DB:B2", "5C:D7:B3:60:FE:5C", "7C:A7:37:7B:8C:1C", "46:C1:A0:93:5B:74", "49:78:45:57:E5:1D", "42:5D:B3:34:DF:71", "7E:E4:60:27:46:9C", "53:B2:12:36:D2:C3", "90:CE:B8:04:19:5B", "49:8F:15:AD:72:71", "78:45:8B:5A:84:44", "00:C3:F4:82:A5:CE", "58:42:D5:C6:39:A9", "F7:BC:6B:D6:83:3D", "74:31:73:6A:A4:7C", "6A:EE:D5:E6:D5:85", "6A:6B:EB:73:2E:99", "78:FB:DC:47:7B:68", "43:02:DF:5B:61:E7", "4C:88:AC:3A:9D:6C"]:
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
    
    try:
        # Start scanning first
        await scanner.start()
        
        # Create status update task
        status_task = asyncio.create_task(status_update())
        
        # Wait forever or until interrupted
        while True:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        print("🔚 Stopping scanner.")
        status_task.cancel()  # Cancel the status update task
        await scanner.stop()  # Stop the scanner gracefully

if __name__ == "__main__":
    asyncio.run(main())
