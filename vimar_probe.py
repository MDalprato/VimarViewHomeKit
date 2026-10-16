#!/usr/bin/env python3
"""Single read-only Qualvision protocol probe; Vimar auth is not yet verified."""
import argparse
import getpass
import hashlib
import ipaddress
import json
import ssl
import sys
import urllib.error
import urllib.request
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='10.0.1.192')
    parser.add_argument('--username', default='adminapp2',
                        help='From the Allo reference; not confirmed for Vimar QR login')
    parser.add_argument('--password-prompt', action='store_true',
                        help='Ask once for a secret locally, without echo or storage')
    parser.add_argument('--encoding', choices=['sha256', 'raw'], default='sha256',
                        help='Candidate password encoding; not verified on Vimar')
    args = parser.parse_args()
    if not ipaddress.ip_address(args.host).is_private:
        parser.error('Use a private LAN IP address')
    password = ''
    if args.password_prompt:
        password = getpass.getpass('Password/token dispositivo (non salvato): ')
        if not password:
            parser.error('Password vuota: nessuna richiesta inviata')
        if args.encoding == 'sha256':
            password = hashlib.sha256(password.encode()).hexdigest()
    # Device uses a self-signed Qualvision certificate. This context is used
    # only for the explicitly selected LAN IP; redirects and proxies disabled.
    ctx = ssl._create_unverified_context()

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect(),
        urllib.request.HTTPSHandler(context=ctx))
    request = urllib.request.Request(
        f'https://{args.host}/tdkcgi', data=envelope(args.username, password),
        headers={'Content-Type': 'application/xml;charset=utf-8'})
    try:
        with opener.open(request, timeout=8) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError('Response too large')
            root = ET.fromstring(raw)
            code = root.findtext('./body/error')
            print(json.dumps({'host': args.host, 'http_status': response.status,
                              'protocol_error': code,
                              'record_session_present': root.find('.//record/id') is not None}))
            # Never print credentials, session identifiers, or record contents.
            if code == '401':
                print('Autenticazione richiesta o rifiutata; nessun retry.', file=sys.stderr)
                return 2
            if code != '0':
                return 3
            return 0
    except (OSError, ValueError, ET.ParseError) as exc:
        print(f'Probe failed: {type(exc).__name__}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
