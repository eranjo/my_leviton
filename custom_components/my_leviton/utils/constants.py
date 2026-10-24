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
    {"name": "Olkkari Seinä", "type": 0x26, "channel": 0x03, "device_id": 0x0E40, "dimmer": False},
    {"name": "Olkkari Katto", "type": 0x26, "channel": 0x43, "device_id": 0x0E40, "dimmer": False},
    {"name": "Alakerta Kylpyhuone Tuuletin", "type": 0x26, "channel": 0x83, "device_id": 0x0E40, "dimmer": False},
    {"name": "Alakerta Kylpyhuone Valot", "type": 0x26, "channel": 0xC3, "device_id": 0x0E40, "dimmer": False},
    {"name": "Pikkueteinen", "type": 0x26, "channel": 0x03, "device_id": 0x0E62, "dimmer": False},
    {"name": "Portaat", "type": 0x26, "channel": 0x43, "device_id": 0x0E62, "dimmer": False},
    {"name": "Yläkerta Kylpyhuone Katto", "type": 0x26, "channel": 0x83, "device_id": 0x0E62, "dimmer": False},
    {"name": "Tiskipöytä", "type": 0x26, "channel": 0xC3, "device_id": 0x0E62, "dimmer": False},
    {"name": "Casper Katto (dimmer)", "type": 0x1E, "channel": 0x03, "device_id": 0x0FCB, "dimmer": True},
    {"name": "Calla Katto (dimmer)", "type": 0x1E, "channel": 0x03, "device_id": 0x0FA1, "dimmer": True},
    {"name": "30200617 (dimmer)", "type": 0x1E, "channel": 0x03, "device_id": 0x0FA9, "dimmer": True},
    {"name": "Työhuone katto", "type": 0x1E, "channel": 0x03, "device_id": 0x1052, "dimmer": True},
    {"name": "TV aula", "type": 0x1E, "channel": 0x03, "device_id": 0x0FB0, "dimmer": True},
    {"name": "Yläkerran Puhallin", "type": 0x2E, "channel": 0x03, "device_id": 0x0D8D, "dimmer": True},
    {"name": "Keittiö Pöytä", "type": 0x26, "channel": 0x03, "device_id": 0x0F70, "dimmer": False},
    {"name": "Keittiö Sivupöytä", "type": 0x26, "channel": 0x43, "device_id": 0x0F70, "dimmer": False},
    {"name": "Keittiö Seinä", "type": 0x26, "channel": 0x83, "device_id": 0x0F70, "dimmer": False},
    {"name": "Makkari Katto", "type": 0x26, "channel": 0xC3, "device_id": 0x0F70, "dimmer": False},
    {"name": "38200559 channel1", "type": 0x26, "channel": 0x03, "device_id": 0x0F6F, "dimmer": False},
    {"name": "Eteinen", "type": 0x26, "channel": 0x43, "device_id": 0x0F6F, "dimmer": False},
    {"name": "Yläkerran kylppärin peilivalo", "type": 0x26, "channel": 0x83, "device_id": 0x0F6F, "dimmer": False},
    {"name": "Käytävä", "type": 0x26, "channel": 0xC3, "device_id": 0x0F6F, "dimmer": False},
    {"name": "Calla vaatehuone", "type": 0x26, "channel": 0x03, "device_id": 0x0F78, "dimmer": False},
    {"name": "Casper Vaatehuone", "type": 0x26, "channel": 0x43, "device_id": 0x0F78, "dimmer": False},
    {"name": "Työhuone komero", "type": 0x26, "channel": 0x83, "device_id": 0x0F78, "dimmer": False},
    {"name": "Kattoparrut", "type": 0x26, "channel": 0xC3, "device_id": 0x0F78, "dimmer": False},
]
