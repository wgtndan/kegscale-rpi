import asyncio
from bleak import BleakScanner
from google.cloud import firestore
from google.oauth2 import service_account
import datetime
import binascii

FIREBASE_CREDS = "serviceAccountKey.json"
EDDYSTONE_UUID_SUFFIX = "e4be"  # Match any service UUID that ends in E4BE

# Setup Firebase Firestore
creds = service_account.Credentials.from_service_account_file(FIREBASE_CREDS)
db = firestore.Client(credentials=creds, project=creds.project_id)

scan_collection = db.collection("ble_scans")
error_collection = db.collection("ble_scan_errors")

def parse_service_data(service_data: bytes):
    return binascii.hexlify(service_data).decode().upper()

async def main():
    print("🔍 Scanning for Eddystone (E4BE) devices...")
    matched = False

    try:
        devices = await BleakScanner.discover(timeout=10)

        if not devices:
            error_collection.add({
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "error": "No BLE devices found"
            })
            print("⚠️ No BLE devices found.")
            return

        for d in devices:
            service_data = d.metadata.get("service_data", {})
            for uuid, data in service_data.items():
                if uuid.lower().endswith(EDDYSTONE_UUID_SUFFIX):
                    matched = True
                    hex_data = parse_service_data(data)
                    print(f"🛰️ {d.name or 'Unknown'} ({d.address}) - Service Data: {hex_data}")

                    scan_collection.add({
                        "timestamp": datetime.datetime.utcnow().isoformat(),
                        "device_name": d.name or "Unknown",
                        "device_id": d.address,
                        "service_data": hex_data
                    })

        if not matched:
            error_collection.add({
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "error": "No Eddystone (E4BE) service data found among discovered devices"
            })
            print("⚠️ Devices found, but none with E4BE service data.")

        print("✅ Done.")

    except Exception as e:
        error_collection.add({
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "error": f"Script exception: {str(e)}"
        })
        print(f"💥 Exception: {e}")

if __name__ == "__main__":
    asyncio.run(main())
