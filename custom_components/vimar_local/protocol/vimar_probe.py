import xml.etree.ElementTree as ET
from datetime import datetime, timedelta


def envelope(username, password):
    root = ET.Element('envelope')
    header = ET.SubElement(root, 'header')
    for key, value in [('username', username), ('password', password),
                       ('passwordencode', '1'), ('security', 'username')]:
        ET.SubElement(header, key).text = value
    body = ET.SubElement(root, 'body')
    ET.SubElement(body, 'command').text = 'get.record.session'
    record = ET.SubElement(ET.SubElement(body, 'content'), 'record')
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    for key, value in [('filetype', 'picture'), ('occurtype', 'all'),
                       ('channels', '1'), ('stream', 'all'),
                       ('starttime', start.isoformat()),
                       ('endtime', (start + timedelta(days=1)).isoformat())]:
        ET.SubElement(record, key).text = value
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)
