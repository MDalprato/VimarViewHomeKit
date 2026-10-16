#!/usr/bin/env python3
"""Experimental Vimar relay control. No actuation unless --open is supplied."""
import argparse
import base64
import getpass
import hashlib
import os
import sys
import xml.etree.ElementTree as ET

from vimar_probe import envelope
from vimar_watch import Client


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='10.0.1.192')
    parser.add_argument('--channel', type=int, choices=(1, 2), default=2)
    parser.add_argument('--relay', type=int, choices=(1, 2),
                        help='Numero protocollo; corrispondenza cancello/serratura da verificare')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--open', action='store_true', help='Invia una vera apertura')
    actions.add_argument('--status', action='store_true', help='Legge solo lo stato generale')
    parser.add_argument('--separate-unlock-password', action='store_true',
                        help='Chiede un PIN di apertura diverso dalla password del monitor')
    args = parser.parse_args(argv)
    if args.open and args.relay is None:
        parser.error('--open richiede --relay 1 oppure --relay 2')
    if not args.open and not args.status:
        print(f'Simulazione: targa {args.channel}, relè {args.relay or "non selezionato"}.')
        print('Nessuna connessione. Usa --status per leggere o --relay N --open per aprire.')
        return 0
    sent = False
    try:
        password = os.environ.get('VIMAR_PASSWORD') or getpass.getpass('Password monitor: ')
        client = Client(args.host, password)
        status = client.send(request(client.secret, 'get.device.status'))
        if args.status:
            value = status.findtext('.//devicestatus/lockstatus', 'non disponibile')
            print(f'lockstatus generale: {value}')
            print('Non indica lo stato fisico della porta né identifica il relè della targa 2.')
            return 0
        unlock = os.environ.get('VIMAR_UNLOCK_PASSWORD')
        if unlock is None:
            unlock = (getpass.getpass('Password/PIN apertura: ')
                      if args.separate_unlock_password else password)
        ability = status.findtext('.//devability', '')
        # Validate before marking the action as sent.
        encode_unlock_password(unlock, ability)
        print(f'Invio apertura: targa {args.channel}, relè {args.relay}.', flush=True)
        sent = True
        open_relay(client, args.channel, args.relay, unlock, ability)
        print('Comando accettato dal monitor. Verifica fisicamente quale uscita è stata azionata.')
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        # Do not print responses, XML or credentials.
        print(f'Operazione non completata ({type(exc).__name__}).', file=sys.stderr)
        if sent:
            print('Esito fisico non verificato. Nessun tentativo automatico ripetuto.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
