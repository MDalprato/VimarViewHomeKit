#!/usr/bin/env python3
"""Browse recording metadata on the monitor. Does not download or delete files."""
import argparse
from datetime import date, datetime, timedelta
import getpass
import json
import os
import sys
import xml.etree.ElementTree as ET
from vimar_watch import Client, ProtocolError


def select_records(records, start, end, channel=None):
    # Compare device wall dates: its Z suffix is not a reliable UTC indication.
    unique = {r['id']: r for r in records
              if start.isoformat() <= r['starttime'][:10] <= end.isoformat()
              and (channel is None or r['channel'] == channel)}
    return sorted(unique.values(), key=lambda r: (r['starttime'], r['id']), reverse=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host', default='10.0.1.192')
    p.add_argument('--from', dest='start', type=date.fromisoformat, default=date(2000, 1, 1))
    p.add_argument('--to', dest='end', type=date.fromisoformat, default=date.today())
    p.add_argument('--channel', choices=['1', '2'])
    p.add_argument('--kind', choices=['video', 'picture', 'both'], default='both')
    p.add_argument('--json', action='store_true')
    args = p.parse_args()
    if args.start > args.end or args.end == date.max:
        p.error('Invalid date range')
    password = os.environ.get('VIMAR_PASSWORD') or getpass.getpass('Password dispositivo (non salvata): ')
    if not password:
        p.error('Empty password')
    try:
        client = Client(args.host, password)
        records = []
        for kind in (('video', 'picture') if args.kind == 'both' else (args.kind,)):
            records.extend(client.records(kind, f'{args.start}T00:00:00',
                                          f'{args.end + timedelta(days=1)}T00:00:00'))
        records = select_records(records, args.start, args.end, args.channel)
    except (OSError, ValueError, ET.ParseError, ProtocolError) as exc:
        print(f'Listing failed: {type(exc).__name__}' +
              (f' ({exc})' if isinstance(exc, ProtocolError) else ''), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(records, indent=2))
    else:
        print('DATA/ORA MONITOR     CANALE TIPO     DURATA   CODIFICA  ID')
        for r in records:
            duration = '-'
            if r['endtime']:
                try:
                    delta = datetime.fromisoformat(r['endtime']) - datetime.fromisoformat(r['starttime'])
                    duration = f'{delta.total_seconds():.0f}s'
                except ValueError:
                    pass
            print(f"{r['starttime'][:19].replace('T', ' '):19} {r['channel']:>6} "
                  f"{r['filetype']:<8} {duration:>6} {r['codec']:<9} {r['id']}")
        print(f'\n{len(records)} registrazioni restituite. Orari del monitor; nessun file scaricato.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
