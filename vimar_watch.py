#!/usr/bin/env python3
"""Experimental local recording watcher. New recordings are NOT verified rings."""
import argparse
import getpass
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from vimar_probe import envelope


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


class Tracker:
    def __init__(self):
        self.watermark = {}

    def update(self, kind, records):
        newest = max((r['starttime'] for r in records), default='')
        if kind not in self.watermark:
            # Do not mix the host clock with device timestamps: this monitor
            # labels local wall time with Z. An initially empty list has no
            # reliable device-time baseline.
            self.watermark[kind] = newest
            return []
        previous = self.watermark[kind]
        self.watermark[kind] = max(previous, newest)
        # A burst of pictures at the same timestamp yields one event per poll.
        unique = {(r['starttime'], r['channel']): r for r in records if r['starttime'] > previous}
        return [unique[key] for key in sorted(unique)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='10.0.1.X')
    parser.add_argument('--interval', type=float, default=10)
    parser.add_argument('--duration', type=float, default=120)
    parser.add_argument('--channel', default='2', help='Outdoor station channel observed in the ring tests')
    parser.add_argument('--kind', choices=['picture', 'video', 'both'], default='video')
    parser.add_argument('--bell', action='store_true', help='Emit a terminal bell on a new recording')
    parser.add_argument('--verbose', action='store_true', help='Print metadata at every poll')
    parser.add_argument('--once', action='store_true', help='List metadata once without detecting events')
    args = parser.parse_args()
    if args.interval < 5 or args.duration < 0:
        parser.error('Interval must be >=5 seconds and duration >=0 (0 means unlimited)')
    password = os.environ.get('VIMAR_PASSWORD') or getpass.getpass('Password dispositivo (non salvata): ')
    if not password:
        parser.error('Empty password')
    client, tracker = Client(args.host, password), Tracker()
    deadline = time.monotonic() + args.duration if args.duration else float('inf')
    kinds = ('picture', 'video') if args.kind == 'both' else (args.kind,)
    try:
        while True:
            for kind in kinds:
                records = [r for r in client.records(kind) if r['channel'] == args.channel]
                if args.once or args.verbose or kind not in tracker.watermark:
                    print(json.dumps({'event': 'scan', 'kind': kind, 'count': len(records),
                                      'records': records}), flush=True)
                for record in tracker.update(kind, records):
                    print(json.dumps({'event': 'record_added', 'record': record,
                                      'possible_ring': True,
                                      'observed_at': datetime.now().astimezone().isoformat()}), flush=True)
                    if args.bell:
                        print('\a', end='', file=sys.stderr, flush=True)
            if args.once or time.monotonic() >= deadline:
                break
            time.sleep(min(args.interval, max(0, deadline - time.monotonic())))
    except (OSError, ValueError, ET.ParseError, ProtocolError) as exc:
        print(f'Stopped: {type(exc).__name__}' +
              (f' ({exc})' if isinstance(exc, ProtocolError) else ''), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
