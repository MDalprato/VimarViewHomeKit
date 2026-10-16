import hashlib
import ssl
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from .vimar_probe import envelope


class ProtocolError(Exception):
    pass

class Client:
    def __init__(self, host, password):
        import ipaddress
        if not ipaddress.ip_address(host).is_private:
            raise ValueError('Private LAN address required')
        self.url = f'https://{host}/tdkcgi'
        self.secret = hashlib.sha256(password.encode()).hexdigest()
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))

    def send(self, root):
        req = urllib.request.Request(self.url, data=ET.tostring(root),
                                     headers={'Content-Type': 'application/xml;charset=utf-8'})
        with self.opener.open(req, timeout=8) as response:
            raw = response.read(1048577)
        if len(raw) > 1048576:
            raise ProtocolError('Response exceeds 1 MiB')
        result = ET.fromstring(raw)
        code = result.findtext('./body/error')
        if code != '0':
            raise ProtocolError(f'Device error {code}')
        return result

    def records(self, kind, start=None, end=None, *, include_locator=False):
        root = ET.fromstring(envelope('adminapp2', self.secret))
        root.find('.//filetype').text = kind
        if start is not None:
            root.find('.//starttime').text = start
        if end is not None:
            root.find('.//endtime').text = end
        response = self.send(root)
        session = response.findtext('.//record/id')
        if not session:
            raise ProtocolError('No record session')
        root.find('./body/command').text = 'get.record.message'
        content = root.find('./body/content')
        content.clear()
        ET.SubElement(ET.SubElement(content, 'record'), 'id').text = session
        records = []
        for _ in range(20):
            response = self.send(root)
            page = response.findall('.//data')
            if not page:
                return records
            for item in page:
                data = {child.tag: child.text or '' for child in item}
                timestamp = data.get('starttime', '').upper()
                # Require valid device timestamps; do not turn malformed pages into events.
                datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                record = {
                    'id': hashlib.sha256(data.get('filename', '').encode()).hexdigest()[:20],
                    'starttime': timestamp, 'filetype': data.get('filetype'),
                    'occurtype': data.get('occurtype'), 'channel': data.get('channel'),
                    'endtime': data.get('endtime', '').upper(),
                    'codec': data.get('codetype', ''),
                }
                if include_locator:
                    record['filename'] = data.get('filename', '').strip()
                    record['address'] = {x.tag: (x.text or '').strip()
                                         for x in item.findall('./address/*')}
                    record['filesize'] = data.get('filesize', '').strip()
                records.append(record)
        raise ProtocolError('Pagination limit reached; partial result discarded')
