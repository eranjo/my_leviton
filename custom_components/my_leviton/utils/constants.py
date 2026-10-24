# utils/constants.py

# USB Vendor ID and Product ID (Microchip-based Leviton VRUSB)
VID = 0x04D8
PID = 0xFE93

# Ask the stick for status of all devices (from original polling code).
POLL_COMMAND = [
    0x02,
    0x0F,
    0x01,
    0x7E,
    0x25,
    0x0D,
    0xBC,
    0x00,
    0xFF,
    0xFF,
    0xFF,
    0xFF,
    0x02,
    0x01,
    0x00,
    0xFE,
    0x7F,
    0x45,
]

# Lights are addressed by (type, channel, device_id). Packets are built at
# runtime with CRC-8/MAXIM — see utils/protocol.py.
# type 0x26 = relay/switch, 0x1E/0x2E = dimmer (level 0-100).

LIGHTS = [
    {"name": "Living Room Wall", "type": 0x26, "channel": 0x03, "device_id": 0xA101, "dimmer": False},
    {"name": "Living Room Ceiling", "type": 0x26, "channel": 0x43, "device_id": 0xA101, "dimmer": False},
    {"name": "Downstairs Bath Fan", "type": 0x26, "channel": 0x83, "device_id": 0xA101, "dimmer": False},
    {"name": "Downstairs Bath Lights", "type": 0x26, "channel": 0xC3, "device_id": 0xA101, "dimmer": False},
    {"name": "Small Entry", "type": 0x26, "channel": 0x03, "device_id": 0xA102, "dimmer": False},
    {"name": "Stairs", "type": 0x26, "channel": 0x43, "device_id": 0xA102, "dimmer": False},
    {"name": "Upstairs Bath Ceiling", "type": 0x26, "channel": 0x83, "device_id": 0xA102, "dimmer": False},
    {"name": "Dish Counter", "type": 0x26, "channel": 0xC3, "device_id": 0xA102, "dimmer": False},
    {"name": "Bedroom A Ceiling (dimmer)", "type": 0x1E, "channel": 0x03, "device_id": 0xA201, "dimmer": True},
    {"name": "Bedroom B Ceiling (dimmer)", "type": 0x1E, "channel": 0x03, "device_id": 0xA202, "dimmer": True},
    {"name": "Spare Dimmer", "type": 0x1E, "channel": 0x03, "device_id": 0xA203, "dimmer": True},
    {"name": "Office Ceiling", "type": 0x1E, "channel": 0x03, "device_id": 0xA204, "dimmer": True},
    {"name": "TV Hall", "type": 0x1E, "channel": 0x03, "device_id": 0xA205, "dimmer": True},
    {"name": "Hallway Fan", "type": 0x2E, "channel": 0x03, "device_id": 0xA301, "dimmer": True},
    {"name": "Kitchen Table", "type": 0x26, "channel": 0x03, "device_id": 0xA401, "dimmer": False},
    {"name": "Kitchen Side Table", "type": 0x26, "channel": 0x43, "device_id": 0xA401, "dimmer": False},
    {"name": "Kitchen Wall", "type": 0x26, "channel": 0x83, "device_id": 0xA401, "dimmer": False},
    {"name": "Master Bedroom Ceiling", "type": 0x26, "channel": 0xC3, "device_id": 0xA401, "dimmer": False},
    {"name": "Spare Switch channel1", "type": 0x26, "channel": 0x03, "device_id": 0xA402, "dimmer": False},
    {"name": "Entry Switch", "type": 0x26, "channel": 0x43, "device_id": 0xA402, "dimmer": False},
    {"name": "Upstairs Bath Mirror", "type": 0x26, "channel": 0x83, "device_id": 0xA402, "dimmer": False},
    {"name": "Hallway", "type": 0x26, "channel": 0xC3, "device_id": 0xA402, "dimmer": False},
    {"name": "Bedroom B Closet", "type": 0x26, "channel": 0x03, "device_id": 0xA403, "dimmer": False},
    {"name": "Bedroom A Closet", "type": 0x26, "channel": 0x43, "device_id": 0xA403, "dimmer": False},
    {"name": "Office Closet", "type": 0x26, "channel": 0x83, "device_id": 0xA403, "dimmer": False},
    {"name": "Ceiling Beams", "type": 0x26, "channel": 0xC3, "device_id": 0xA403, "dimmer": False},
]
