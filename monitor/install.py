#!/usr/bin/env python3
"""Install a private per-user runtime outside macOS protected Documents."""
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

source = Path(__file__).resolve().parent
base = Path.home() / 'Library/Application Support/FomoMonitor'
runtime = base / 'app'
data = base / 'data'
for directory in (base, runtime, data, data / 'records'):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
os.umask(0o077)
# One-time migration: preserve the existing refresh target outside public source.
thread_file = data / 'thread_id'
old_server = runtime / 'server.py'
if not thread_file.exists() and old_server.exists():
    import re
    match = re.search(r"^THREAD = '([^']+)'", old_server.read_text(), re.MULTILINE)
    if match:
        thread_file.write_text(match.group(1))
        thread_file.chmod(0o600)
for file in ('server.py', 'store.py', 'feishu.py', 'telegram_client.py', 'portfolio.py', 'simulation.py', 'update_checker.py', 'VERSION'):
    shutil.copyfile(source / file, runtime / file)
shutil.copytree(source / 'static', runtime / 'static', dirs_exist_ok=True)
for file in source.parent.glob('*监测*.md'):
    shutil.copyfile(file, data / 'records' / file.name)
plist = Path.home() / 'Library/LaunchAgents/club.local.fomo-monitor.plist'
plist.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(['launchctl', 'bootout', 'gui/' + str(os.getuid()), str(plist)], capture_output=True)
settings = {'Label':'club.local.fomo-monitor',
            'ProgramArguments':['/usr/bin/caffeinate','-i','/usr/bin/python3',str(runtime/'server.py')],
            'WorkingDirectory':str(runtime),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':15,
            'StandardOutPath':str(data/'service.log'),'StandardErrorPath':str(data/'service-error.log')}
plist.write_bytes(plistlib.dumps(settings))
plist.chmod(0o600)
subprocess.run(['launchctl', 'bootstrap', 'gui/' + str(os.getuid()), str(plist)], check=True)
print('已安装并启动本机服务：http://127.0.0.1:8765')
