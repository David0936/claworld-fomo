"""Durable local notification outbox; shared by dashboard and sampling agent."""
import json
import os
import sqlite3
import time
from pathlib import Path

DATA = Path(os.environ.get('FOMO_DATA_DIR', Path.home() / 'Library/Application Support/FomoMonitor/data'))

def connect():
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    db = sqlite3.connect(str(DATA / 'monitor.sqlite3'), timeout=15)
    db.row_factory = sqlite3.Row
    db.executescript('''
    CREATE TABLE IF NOT EXISTS events (
      id TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
      next_attempt REAL NOT NULL DEFAULT 0, error TEXT, sent REAL);
    CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS tokens (id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS samples (token_id TEXT NOT NULL, observed_at TEXT NOT NULL,
      payload TEXT NOT NULL, PRIMARY KEY(token_id,observed_at));
    CREATE TABLE IF NOT EXISTS archives (
      token_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS deliveries (
      event_id TEXT NOT NULL, channel TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
      attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
      error TEXT, sent REAL, PRIMARY KEY(event_id, channel));
    ''')
    return db

def state_set(key, value):
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))

def state_get(key, default=None):
    with connect() as db:
        row = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

def enqueue(event):
    required = ('id', 'kind', 'text', 'observed_at')
    if any(not isinstance(event.get(k), str) or not event[k].strip() for k in required):
        raise ValueError('事件必须包含 id、kind、text、observed_at')
    if len(event['text']) > 12000 or len(event['id']) > 300:
        raise ValueError('事件过长')
    if event['kind'] not in ('hit', 'milestone', 'risk', 'stopped', 'strategy', 'outage', 'recovery', 'test'):
        raise ValueError('未知事件类型')
    if 'channels' in event and (not isinstance(event['channels'], list) or not event['channels']
                               or any(c not in ('feishu','telegram') for c in event['channels'])):
        raise ValueError('未知推送通道')
    if event['kind'] == 'hit':
        if event.get('source') != 'windvane' or event.get('verified') is not True:
            raise ValueError('新命中必须由 Windvane 核验')
        # An interval must use its lower bound; missing/uncertain data cannot pass.
        for key, low, high in [('age_days', 1, 7), ('market_cap', 200000, 500000), ('fomo_ratio', 15, 100)]:
            value = event.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                raise ValueError('新命中不符合筛选条件：' + key)
        if not event.get('chain') or len(event.get('ca', '')) < 32:
            raise ValueError('缺少链或完整 CA')
    with connect() as db:
        result = db.execute('INSERT OR IGNORE INTO events(id,created,payload) VALUES (?,?,?)',
                            (event['id'], time.time(), json.dumps(event, ensure_ascii=False)))
        return result.rowcount == 1

def snapshot():
    with connect() as db:
        rows = db.execute('SELECT * FROM events ORDER BY created DESC LIMIT 100').fetchall()
        counts = {r[0]: r[1] for r in db.execute('SELECT status,count(*) FROM events GROUP BY status')}
        deliveries = [dict(r) for r in db.execute('SELECT * FROM deliveries WHERE event_id IN (SELECT id FROM events ORDER BY created DESC LIMIT 100)')]
    return {'events': [dict(r, payload=json.loads(r['payload']), deliveries=[d for d in deliveries if d['event_id']==r['id']]) for r in rows], 'counts': counts,
            'scan': state_get('scan', {'status': 'waiting', 'detail': '等待第一轮结构化监测结果'}),
            'refresh': state_get('refresh'), 'delivery': state_get('delivery')}
