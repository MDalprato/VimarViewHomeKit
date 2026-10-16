import base64
import hashlib
import xml.etree.ElementTree as ET
from .vimar_probe import envelope


def request(secret, command, fields=()):
    root = ET.fromstring(envelope('adminapp2', secret))
    root.find('./body/command').text = command
    content = root.find('./body/content')
    content.clear()
    for name, value in fields:
        ET.SubElement(content, name).text = str(value)
    return root

def encode_unlock_password(password, ability):
    # VIEW Door: QvDeviceApi.deviceUnlock checks capability bit 24.
    flags = base64.b64decode(ability, validate=True)
    if len(flags) < 4:
        raise ValueError('Capacità del dispositivo mancanti o incomplete')
    if flags[3] & 1 and len(password) < 64:
        return hashlib.sha256(password.encode()).hexdigest()
    return password

def open_relay(client, channel, relay, password, ability):
    if channel not in (1, 2) or relay not in (1, 2):
        raise ValueError('Targa e relè devono essere 1 o 2')
    # Exact XML element names from DeviceUnlockContent annotations in VIEW Door.
    root = request(client.secret, 'set.device.opendoor', (
        ('door', channel), ('locknumber', relay),
        ('password', encode_unlock_password(password, ability))))
    # Exactly one request: never retry an actuation after a timeout.
    return client.send(root)
