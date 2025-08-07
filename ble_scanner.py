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

TARGET_UUIDS = ["e4be", "feaa"]  # Custom UUID and standard Eddystone UUID

# === Firebase Collections ===
success_collection = db.collection("ble_scans")
failure_collection = db.collection("ble_errors")

def process_packet(data):
    try:
        ev = aiobs.HCI_Event()
        xx = ev.decode(data)
        
        # Get basic device info
        peer_info = ev.retrieve("peer")
        peer_address = peer_info[0].val if peer_info else "unknown"
        
        device_name = ev.retrieve("Complete Local Name")
        name = device_name[0].val if device_name else "Unknown"
        
        # Look for service data
        service_data = ev.retrieve("Service Data")
        if service_data:
            for sd in service_data:
                uuid = sd.uuid.lower()
                if any(target_uuid in uuid for target_uuid in TARGET_UUIDS):
                    hex_data = binascii.hexlify(sd.payload).decode().upper()
                    now = datetime.utcnow().isoformat() + "Z"
                    
                    print(f"📡 {peer_address} | {name} | UUID: {uuid} | {hex_data}")
                    
                    # Parse based on UUID type
                    frame_type = "Unknown"
                    parsed_data = {}
                    
                    if uuid == "feaa" and len(sd.payload) > 0:
                        # Standard Eddystone parsing
                        frame_type_byte = sd.payload[0]
                        if frame_type_byte == 0x00:  # Eddystone-UID
                            frame_type = "Eddystone-UID"
                            if len(sd.payload) >= 18:
                                namespace_id = binascii.hexlify(sd.payload[2:12]).decode().upper()
                                instance_id = binascii.hexlify(sd.payload[12:18]).decode().upper()
                                tx_power = sd.payload[1] - 256 if sd.payload[1] > 127 else sd.payload[1]
                                parsed_data = {
                                    "namespace_id": namespace_id,
                                    "instance_id": instance_id,
                                    "tx_power": tx_power
                                }
                        elif frame_type_byte == 0x10:  # Eddystone-URL
                            frame_type = "Eddystone-URL"
                            # URL parsing would go here
                        elif frame_type_byte == 0x20:  # Eddystone-TLM
                            frame_type = "Eddystone-TLM"
                            # Telemetry parsing would go here
                    elif "e4be" in uuid and len(sd.payload) > 0:
                        # Custom beacon parsing
                        frame_type = "Custom-E4BE"
                        parsed_data = {
                            "payload_length": len(sd.payload),
                            "first_byte": f"0x{sd.payload[0]:02X}" if len(sd.payload) > 0 else None
                        }
                    
                    doc = {
                        "timestamp": now,
                        "device_address": peer_address,
                        "device_name": name,
                        "service_uuid": uuid,
                        "frame_type": frame_type,
                        "service_data_raw": hex_data,
                        "parsed_data": parsed_data
                    }
                    success_collection.add(doc)
                    
    except Exception as e:
        now = datetime.utcnow().isoformat() + "Z"
        print(f"⚠️ Error processing packet: {str(e)}")
        failure_collection.add({
            "timestamp": now,
            "error": str(e),
            "raw_data": binascii.hexlify(data).decode() if data else "No data"
        })

async def main():
    print("🔍 Listening for BLE advertisements...")
    print(f"🎯 Looking for UUIDs: {', '.join(TARGET_UUIDS)}")
    
    try:
        # Method 1: Try the standard aioblescan approach
        event_loop = asyncio.get_event_loop()
        
        # Create socket and connection
        mysocket = aiobs.create_bt_socket(0)
        fac = aiobs.BLEScanRequester()
        fac.process = process_packet
        
        conn_coro = event_loop.create_connection(lambda: fac, sock=mysocket)
        transport, protocol = await conn_coro
        
        print("✅ Scanner started successfully")
        
        # Keep the scanner running
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("🔚 Stopping scanner...")
            
    except Exception as e:
        print(f"❌ Primary method failed: {e}")
        print("🔄 Trying alternative method...")
        
        try:
            # Method 2: Direct socket reading approach
            mysocket = aiobs.create_bt_socket(0)
            mysocket.setblocking(False)  # Make socket non-blocking
            
            print("✅ Alternative scanner started")
            
            while True:
                try:
                    data = mysocket.recv(1024)
                    if data:
                        process_packet(data)
                except BlockingIOError:
                    # No data available, continue
                    await asyncio.sleep(0.1)
                except Exception as recv_error:
                    print(f"⚠️ Receive error: {recv_error}")
                    await asyncio.sleep(1)
                    
        except KeyboardInterrupt:
            print("🔚 Stopping alternative scanner...")
        except Exception as alt_error:
            print(f"❌ Alternative method failed: {alt_error}")
            print("💡 Try running with sudo, or check if Bluetooth is enabled")
            
            # Log the error to Firebase
            now = datetime.utcnow().isoformat() + "Z"
            failure_collection.add({
                "timestamp": now,
                "error": f"All scanner methods failed: {str(e)} | Alt: {str(alt_error)}"
            })
    finally:
        try:
            if 'mysocket' in locals():
                mysocket.close()
            print("🔒 Socket closed")
        except:
            pass

if __name__ == "__main__":
    # Make sure we have the right permissions
    print("🚀 Starting BLE Eddystone Scanner")
    print("⚠️  Make sure to run with sudo for BLE permissions")
    print("⚠️  Make sure Bluetooth is enabled: sudo systemctl enable bluetooth")
    asyncio.run(main())