from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import uuid
import struct

BT_BASE_UUID = uuid.UUID("00000000-0000-1000-8000-00805F9B34FB")

# AD Type constants
_AD_FLAGS                      = 0x01
_AD_UUID16_INCOMPLETE          = 0x02
_AD_UUID16_COMPLETE            = 0x03
_AD_UUID32_INCOMPLETE          = 0x04
_AD_UUID32_COMPLETE            = 0x05
_AD_UUID128_INCOMPLETE         = 0x06
_AD_UUID128_COMPLETE           = 0x07
_AD_LOCAL_NAME_SHORT           = 0x08
_AD_LOCAL_NAME_COMPLETE        = 0x09
_AD_TX_POWER                   = 0x0A
_AD_SOLICIT_UUID16             = 0x14
_AD_SOLICIT_UUID128            = 0x15
_AD_SOLICIT_UUID32             = 0x1F
_AD_SERVICE_DATA_16            = 0x16
_AD_SERVICE_DATA_32            = 0x20
_AD_SERVICE_DATA_128           = 0x21
_AD_MANUFACTURER_SPECIFIC_DATA = 0xFF

@dataclass
class ScanRecord:
    service_uuids: List[uuid.UUID] = field(default_factory=list)
    solicit_uuids: List[uuid.UUID] = field(default_factory=list)
    manufacturer_data: Dict[int, bytes] = field(default_factory=dict)  # company_id -> payload
    service_data: Dict[uuid.UUID, bytes] = field(default_factory=dict) # uuid -> payload
    flags: Optional[int] = None
    tx_power: Optional[int] = None
    local_name: Optional[str] = None
    raw: Optional[bytes] = None

def _bytes_to_uuid_le_128(b: bytes) -> uuid.UUID:
    """Convert 16 raw bytes from AD payload (little-endian) to UUID."""
    if len(b) != 16:
        raise ValueError("need 16 bytes")
    lo = struct.unpack("<Q", b[0:8])[0]
    hi = struct.unpack("<Q", b[8:16])[0]
    return uuid.UUID(int=((hi << 64) | lo))

def _uuid_from_16(v: int) -> uuid.UUID:
    # Build a 128-bit UUID from a 16-bit short using Bluetooth Base UUID
    return uuid.UUID(f"{v:04x}0000-0000-1000-8000-00805f9b34fb")

def _uuid_from_32(v: int) -> uuid.UUID:
    # Build a 128-bit UUID from a 32-bit short using Bluetooth Base UUID
    return uuid.UUID(f"{v:08x}-0000-1000-8000-00805f9b34fb")

def _parse_uuid_list(buf: bytes, size_each: int) -> List[uuid.UUID]:
    out: List[uuid.UUID] = []
    for i in range(0, len(buf), size_each):
        chunk = buf[i:i+size_each]
        if len(chunk) != size_each:
            break
        if size_each == 2:
            (v,) = struct.unpack("<H", chunk)
            out.append(_uuid_from_16(v))
        elif size_each == 4:
            (v,) = struct.unpack("<I", chunk)
            out.append(_uuid_from_32(v))
        elif size_each == 16:
            out.append(_bytes_to_uuid_le_128(chunk))
    return out

def parse_scan_record(ad: bytes) -> ScanRecord:
    """Parse a BLE advertisement (AdvData or ScanRsp payload) into a ScanRecord."""
    i = 0
    sr = ScanRecord(raw=ad)
    while i < len(ad):
        length = ad[i]
        if length == 0:
            break
        ad_type = ad[i+1]
        value = ad[i+2:i+1+length]
        # Dispatch
        if ad_type in (_AD_UUID16_INCOMPLETE, _AD_UUID16_COMPLETE):
            sr.service_uuids.extend(_parse_uuid_list(value, 2))
        elif ad_type in (_AD_UUID32_INCOMPLETE, _AD_UUID32_COMPLETE):
            sr.service_uuids.extend(_parse_uuid_list(value, 4))
        elif ad_type in (_AD_UUID128_INCOMPLETE, _AD_UUID128_COMPLETE):
            sr.service_uuids.extend(_parse_uuid_list(value, 16))
        elif ad_type in (_AD_LOCAL_NAME_SHORT, _AD_LOCAL_NAME_COMPLETE):
            try:
                sr.local_name = value.decode("utf-8", errors="ignore")
            except Exception:
                sr.local_name = None
        elif ad_type == _AD_TX_POWER:
            if len(value) >= 1:
                sr.tx_power = struct.unpack("b", value[:1])[0]
        elif ad_type == _AD_FLAGS:
            if len(value) >= 1:
                sr.flags = value[0]
        elif ad_type == _AD_SOLICIT_UUID16:
            sr.solicit_uuids.extend(_parse_uuid_list(value, 2))
        elif ad_type == _AD_SOLICIT_UUID32:
            sr.solicit_uuids.extend(_parse_uuid_list(value, 4))
        elif ad_type == _AD_SOLICIT_UUID128:
            sr.solicit_uuids.extend(_parse_uuid_list(value, 16))
        elif ad_type == _AD_MANUFACTURER_SPECIFIC_DATA:
            if len(value) >= 2:
                company_id = struct.unpack("<H", value[:2])[0]
                sr.manufacturer_data[company_id] = value[2:]
        elif ad_type in (_AD_SERVICE_DATA_16, _AD_SERVICE_DATA_32, _AD_SERVICE_DATA_128):
            if ad_type == _AD_SERVICE_DATA_16 and len(value) >= 2:
                (svc16,) = struct.unpack("<H", value[:2])
                u = _uuid_from_16(svc16)
                sr.service_data[u] = value[2:]
            elif ad_type == _AD_SERVICE_DATA_32 and len(value) >= 4:
                (svc32,) = struct.unpack("<I", value[:4])
                u = _uuid_from_32(svc32)
                sr.service_data[u] = value[4:]
            elif ad_type == _AD_SERVICE_DATA_128 and len(value) >= 16:
                u = _bytes_to_uuid_le_128(value[:16])
                sr.service_data[u] = value[16:]
        # advance
        i += 1 + length
    return sr
