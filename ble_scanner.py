import asyncio
from bleak import BleakScanner
from google.cloud import firestore
from google.oauth2 import service_account
import datetime

FIREBASE_CREDS = "serviceAccountKey.json"
EDDYSTONE_UUID = "0000e4be-0000-1000-8000-00805f9b34fb"

# Setup Firebase Firestore
creds = service_account.Credentials.from_service_account_file(FIREBASE_CREDS)
db = firestore.Client(credentials=creds, project=creds.project_id)
collection = db.collection("ble_scans")

def parse_service_data(service_data: bytes):
    return service_data.hex().upper()

async def main():
    print("🔍 Scanning for Eddystone (E4BE) devices...")

    devices = await BleakScanner.discover(timeout=10)
    for d in devices:
        adv = getattr(d, "details", {})
        props = adv.get("props", {})

        service_data_dict = props.get("kCBAdvDataServiceData", {})
        service_data = service_data_dict.get(EDDYSTONE_UUID.lower())

        if service_data:
            hex_data = parse_service_data(service_data)
            print(f"🛰️ {d.name or 'Unknown'} ({d.address}) - Service Data: {hex_data}")

            doc = {
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "device_name": d.name or "Unknown",
                "device_id": d.address,
                "service_data": hex_data
            }
            collection.add(doc)

    print("✅ Done.")

if __name__ == "__main__":
    asyncio.run(main())
