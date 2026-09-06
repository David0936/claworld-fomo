#!/usr/bin/env python3
"""Feed verified observations from the existing Codex automation into the local outbox."""
import argparse
import json
import sys
import time
import shutil
from pathlib import Path
from store import DATA, enqueue, state_set, snapshot

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    event = sub.add_parser('event')
    event.add_argument('file', help='UTF-8 JSON file, or - for stdin')
    for name in ('baseline','sample'):
        cmd=sub.add_parser(name)
        cmd.add_argument('file',help='UTF-8 JSON file, or - for stdin')
    scan = sub.add_parser('scan')
    scan.add_argument('status', choices=['running', 'complete', 'partial', 'blocked'])
    scan.add_argument('--detail', required=True)
    scan.add_argument('--chains', default='')
    sub.add_parser('status')
    args = parser.parse_args()
    if args.command in ('event','baseline','sample'):
        raw = sys.stdin.read() if args.file == '-' else Path(args.file).read_text()
        data=json.loads(raw)
        if args.command=='event':
            print(json.dumps({'queued':enqueue(data)}))
        else:
            from portfolio import register,add_sample
            print(json.dumps({'saved':(register if args.command=='baseline' else add_sample)(data)}))
    elif args.command == 'scan':
        records = DATA / 'records'
        records.mkdir(parents=True, exist_ok=True, mode=0o700)
        for file in Path(__file__).resolve().parent.parent.glob('*监测*.md'):
            shutil.copyfile(file, records / file.name)
        state_set('scan', {'status': args.status, 'detail': args.detail,
                          'chains': args.chains.split(',') if args.chains else [], 'updated_at': time.time()})
        print('ok')
    else:
        print(json.dumps(snapshot(), ensure_ascii=False))

if __name__ == '__main__':
    main()
