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

TARGET_TLM_UUID = "E4BE"  # Eddystone TLM frame UUID
TARGET_UID_UUID = "FEAA"  # Eddystone UID frame UUID

def process_packet(device, advertisement_data):
    try:
        # Check if the advertisement contains Eddystone TLM or UID frame
        service_uuids = advertisement_data.get("service_uuids", [])
        
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
                print(f"📡 {peer_address} | {device_na
