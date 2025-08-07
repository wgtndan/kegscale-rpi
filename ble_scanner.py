import asyncio
from bleak import BleakScanner
from google.cloud import firestore
from google.oauth2 import service_account
import datetime
import binascii

# Config
FIREBASE_CREDS = "serviceAccountKey.json"
EDDYSTONE_UUID_SUFFIX = "e4be"

# Setup Firebase
creds = service_account.Credentials.from_service_account_file(FIREBASE_CREDS)
db = firestore.Client(credentials=creds, project=creds.project_id)
success_col = db.collection("ble_scans")
failure_col = db.collection("ble_errors")

async def main():
    print("🔍 Scanning for Eddystone (E4BE) devices...")

    try:
        devices = await BleakScanner.discover(timeout=10.0)

        for d in devices:
            # Linux: service_data is in 'details' dict from BlueZ
            ad = d.details.get("props", {})  # from DBus
            service_data = ad.get("ServiceData", {})

            for uuid, raw_bytes in service_data.items():
                if uuid.lower().endswith(EDDYSTONE_UUID_SUFFIX):
                    hex_data = binascii.hexlify(raw_bytes).decode().upper()

                    print(f"🛰️ {d.name or 'Unknown'} ({d.address}) → {hex_data}")

                    doc = {
                        "timestamp": datetime.datetime.utcnow().isoformat(),
                        "device_name": d.name or "Unknown",
                        "device_id": d.address,
                        "service_data": hex_data,
                    }
                    success_col.add(doc)

        print("✅ Done.")
    except Exception as e:
        print(f"❌ Error: {e}")
        failure_col.add({
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "error": str(e)
        })

if __name__ == "__main__":
    asyncio.run(main())
