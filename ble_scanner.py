import aioblescan as aiobs
import asyncio
from datetime import datetime
import binascii
import firebase_admin
from firebase_admin import credentials, firestore

# === Firebase Setup ===
cred = credentials.Certificate("serviceAccountKey.json")  # Make sure this file is in the same directory
firebase_admin.initialize_app(cred)
db = firestore.client()

TARGET_UUID = "e4be"

# === Firebase Collections ===
success_collection = db.collection("ble_scans")
failure_collection = db.collection("ble_errors")

def process_packet(data):
    try:
        ev = aiobs.HCI_Event()
        ev.decode(data)
        service_data = ev.retrieve("Service Data")
        peer_address = ev.retrieve("peer")[0].val if ev.retrieve("peer") else "unknown"
        device_name = ev.retrieve("Complete Local Name")
        name = device_name[0].val if device_name else "Unknown"

        for sd in service_data:
            uuid = sd.uuid.lower()
            if TARGET_UUID in uuid:
                hex_data = binascii.hexlify(sd.payload).decode().upper()
                now = datetime.utcnow().isoformat() + "Z"
                print(f"📡 {peer_address} | {name} | {hex_data}")
                doc = {
                    "timestamp": now,
                    "device_address": peer_address,
                    "device_name": name,
                    "service_data_raw": hex_data
                }
                success_collection.add(doc)
    except Exception as e:
        now = datetime.utcnow().isoformat() + "Z"
        print(f"⚠️ Error processing packet: {str(e)}")
        failure_collection.add({
            "timestamp": now,
            "error": str(e)
        })

async def main():
    # Create the BLE scanner
    scanner = aiobs.create_bt_socket(0)  # Use 0 for the default Bluetooth adapter
    print("🔍 Listening for BLE advertisements...")

    # Start scanning and process packets
    await scanner.start(process_packet)  # This is how you start the scan in aioblescan 0.2.14

    try:
        while True:
            await asyncio.sleep(3600)  # Keep the service alive for 1 hour (or however long you need)
    except KeyboardInterrupt:
        print("🔚 Stopping scanner.")
        await scanner.stop()  # Stop the scanner gracefully

if __name__ == "__main__":
    asyncio.run(main())
