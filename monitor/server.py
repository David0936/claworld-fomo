#!/usr/bin/env python3
"""Loopback-only Fomo dashboard and durable Feishu delivery worker."""
import fcntl
import hashlib
import json
import os
import secrets
import subprocess
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from store import DATA, connect, enqueue, snapshot, state_get, state_set
from feishu import send_text, authorization_url, exchange_user, _validate_webhook_url
import telegram_client
import update_checker

ROOT = Path(__file__).resolve().parent
ORIGIN = 'http://127.0.0.1:8765'
CONFIG_LOCK = threading.Lock()
REFRESH_LOCK = threading.Lock()
SESSIONS = {}
OAUTH = {}
TG_BINDINGS = {}
TG_LOCK = threading.Lock()
CSRF = secrets.token_urlsafe(32)
CONFIG_KEYS = {'webhook', 'signing_secret', 'app_id', 'app_secret', 'receive_open_id', 'telegram_token', 'telegram_chat_id'}

def config():
    try:
        return json.loads((DATA / 'config.json').read_text())
    except FileNotFoundError:
        return {}

def save_config(patch, expected_telegram_token=None):
    if any(k in patch and not isinstance(patch[k], str) for k in CONFIG_KEYS):
        raise ValueError('配置字段必须为文本')
    if patch.get('webhook'):
        _validate_webhook_url(patch['webhook'].strip())
    if patch.get('telegram_token'):
        telegram_client._token({'telegram_token':patch['telegram_token'].strip()})
    if patch.get('telegram_chat_id'):
        telegram_client._chat_id({'telegram_chat_id':patch['telegram_chat_id'].strip()})
    with CONFIG_LOCK:
        conf = config()
        if expected_telegram_token is not None and conf.get('telegram_token') != expected_telegram_token:
            raise ValueError('机器人已变更，请重新开始绑定')
        if patch.get('telegram_token') and patch['telegram_token'].strip() != conf.get('telegram_token'):
            conf.pop('telegram_chat_id', None)
            conf.pop('telegram_user', None)
        for key in CONFIG_KEYS:
            if key in patch and patch[key]:
                conf[key] = str(patch[key]).strip()
        if patch.get('clear_webhook'):
            conf.pop('webhook', None)
            conf.pop('signing_secret', None)
        if patch.get('clear_telegram'):
            for key in ('telegram_token', 'telegram_chat_id', 'telegram_user'):
                conf.pop(key, None)
        for key in ('feishu_enabled', 'telegram_enabled'):
            if key in patch:
                if not isinstance(patch[key], bool):
                    raise ValueError('通道开关必须为布尔值')
                conf[key] = patch[key]
        conf['redirect_uri'] = ORIGIN + '/auth/callback'
        temp = DATA / 'config.tmp'
        temp.write_text(json.dumps(conf, ensure_ascii=False))
        temp.chmod(0o600)
        temp.replace(DATA / 'config.json')

def configured(conf):
    return bool(active_channels(conf))

def active_channels(conf):
    channels = []
    if conf.get('feishu_enabled', True) and (conf.get('webhook') or all(conf.get(k) for k in ('app_id', 'app_secret', 'receive_open_id'))):
        channels.append('feishu')
    if conf.get('telegram_enabled', True) and conf.get('telegram_token') and conf.get('telegram_chat_id'):
        channels.append('telegram')
    return channels

def delivery_once():
    conf = config()
    channels = active_channels(conf)
    if not channels:
        state_set('delivery', {'status': 'unconfigured', 'detail': '等待启用飞书或 Telegram 推送通道'})
        return
    # Create per-channel receipts once, so one failed destination never resends a successful one.
    with connect() as db:
        rows = db.execute("SELECT * FROM events WHERE status='pending' AND id NOT IN (SELECT event_id FROM deliveries)").fetchall()
        for row in rows:
            event = json.loads(row['payload'])
            targets = event.get('channels', channels)
            for channel in targets:
                db.execute('INSERT OR IGNORE INTO deliveries(event_id,channel) VALUES (?,?)', (row['id'],channel))
    for channel in channels:
        with connect() as db:
            row = db.execute("SELECT d.*,e.payload FROM deliveries d JOIN events e ON e.id=d.event_id WHERE d.channel=? AND d.status='pending' AND d.next_attempt<=? ORDER BY e.created LIMIT 1", (channel,time.time())).fetchone()
        if not row:
            continue
        event = json.loads(row['payload'])
        name = '飞书' if channel == 'feishu' else 'Telegram'
        try:
            sender = send_text if channel == 'feishu' else telegram_client.send_text
            sender(conf, event['text'] + '\n\n观测时间：' + event['observed_at'] + '\n事件编号：' + event['id'])
        except Exception:
            error = name + '发送失败，请检查凭证、接收人及网络；系统将自动重试'
            attempts = row['attempts'] + 1
            with connect() as db:
                db.execute('UPDATE deliveries SET attempts=?,next_attempt=?,error=? WHERE event_id=? AND channel=?',
                           (attempts,time.time()+min(300,5*2**min(attempts,6)),error,row['event_id'],channel))
            state_set('delivery_' + channel, {'status':'error','detail':error,'updated_at':time.time()})
        else:
            with connect() as db:
                db.execute("UPDATE deliveries SET status='sent',sent=?,attempts=attempts+1,error=NULL WHERE event_id=? AND channel=?", (time.time(),row['event_id'],channel))
            state_set('delivery_' + channel, {'status':'ok','detail':name+'已接受最近一次推送','updated_at':time.time()})
        with connect() as db:
            receipts = db.execute('SELECT * FROM deliveries WHERE event_id=?', (row['event_id'],)).fetchall()
            all_sent = all(r['status']=='sent' for r in receipts)
            errors = '；'.join(r['error'] for r in receipts if r['error']) or None
            db.execute('UPDATE events SET status=?,sent=?,attempts=?,error=? WHERE id=?',
                       ('sent' if all_sent else 'pending',time.time() if all_sent else None,sum(r['attempts'] for r in receipts),errors,row['event_id']))
    state_set('delivery', {'status':'ok','detail':'已启用：'+'、'.join('飞书' if c=='feishu' else 'Telegram' for c in channels)})

def worker():
    while True:
        try:
            delivery_once()
        except Exception:
            state_set('delivery', {'status': 'error', 'detail': '推送服务内部异常，请检查本机服务日志'})
        time.sleep(2)

def update_worker():
    while True:
        try:
            state_set('update', update_checker.check(ROOT))
        except Exception:
            if not state_get('update', {}):
                state_set('update', {'status':'error', 'detail':'暂时无法检查项目更新',
                                     'checked_at':time.time()})
        time.sleep(21600)

def thread_id():
    try:
        value = (DATA / 'thread_id').read_text().strip()
        if value:
            return value
    except FileNotFoundError:
        pass
    return os.environ.get('FOMO_CODEX_THREAD_ID', '').strip()

def request_refresh():
    if not REFRESH_LOCK.acquire(blocking=False):
        return {'detail': '刷新请求正在提交，请稍后查看状态'}
    try:
        return _request_refresh()
    finally:
        REFRESH_LOCK.release()

def _request_refresh():
    now = time.time()
    last = state_get('refresh', {})
    scan = state_get('scan', {})
    if now - last.get('at', 0) < 120 or (scan.get('status') == 'running' and now - scan.get('updated_at', 0) < 1800):
        return {'detail': '已有刷新请求或监测正在进行，请等待本轮完成'}
    # Fixed argv: no shell, no user-controlled commands or prompts.
    prompt = ('请立即按现有 Fomo 自动化规则执行一轮真实全链筛选，并遵循 '
              + str(ROOT / 'INTEGRATION.md') + ' 更新扫描状态和推送事件。'
              '用户已授权将合格信号和策略提醒发送至本机配置的飞书和 Telegram 接收方。'
              '每个新命中核验后立刻入队，不要等整轮结束。同时阅读 '
              + str(ROOT / 'PORTFOLIO.md') + '，为新命中登记100U基线，并为已有标的补充本轮实测价格；手动刷新不作为两小时确认。')
    try:
        target = thread_id()
        if not target:
            raise OSError('尚未配置 Codex 监测任务 ID')
        result = subprocess.run(['/Applications/ChatGPT.app/Contents/Resources/codex', 'queue',
                                 '--thread', target, '--message', prompt],
                                capture_output=True, timeout=20, cwd=str(ROOT))
        ok = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    detail = '刷新请求已加入 Codex 队列；以实际核验时间为准' if ok else '无法提交刷新请求，请打开 Codex 中的原监测任务'
    state_set('refresh', {'at': now, 'detail': detail, 'ok': ok})
    return {'detail': detail}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Authorization codes and secrets must not enter access logs.

    def respond(self, value, status=200, content_type='application/json; charset=utf-8', headers=None):
        body = value.encode() if isinstance(value, str) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def host_ok(self):
        return self.headers.get('Host') == '127.0.0.1:8765'

    def user(self):
        try:
            cookies = SimpleCookie(self.headers.get('Cookie', ''))
            session = SESSIONS.get(cookies['session'].value)
            return session['user'] if session and session['expires'] > time.time() else None
        except (KeyError, ValueError):
            return None

    def do_GET(self):
        if not self.host_ok():
            return self.respond({'error': '无效主机'}, 403)
        parsed = urlparse(self.path)
        if parsed.path in ('/', '/app.js', '/style.css', '/portfolio.js'):
            name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css', '/portfolio.js': 'portfolio.js'}[parsed.path]
            kind = {'html': 'text/html', 'js': 'application/javascript', 'css': 'text/css'}[name.split('.')[-1]]
            return self.respond((ROOT / 'static' / name).read_text(), content_type=kind + '; charset=utf-8')
        if parsed.path == '/api/portfolio':
            from portfolio import report
            query=parse_qs(parsed.query)
            try:
                data=report()
            except (ValueError,KeyError):
                return self.respond({'error':'费率或采样数据格式不正确'},400)
            return self.respond(data)
        if parsed.path == '/api/state':
            conf = config()
            data = snapshot()
            data.update(csrf=CSRF, user=self.user(), configured=configured(conf),
                        config={k: bool(conf.get(k)) for k in CONFIG_KEYS},
                        channels=active_channels(conf),
                        enabled={c:conf.get(c+'_enabled',True) for c in ('feishu','telegram')},
                        channel_delivery={c:state_get('delivery_'+c) for c in ('feishu','telegram')},
                        telegram_chat_id=conf.get('telegram_chat_id',''),
                        oauth_ready=bool(conf.get('app_id') and conf.get('app_secret')))
            data['version'] = state_get('update', {'status':'checking',
                                                   'current':update_checker.local_version(ROOT)})
            data['records'] = [{'name': p.name, 'text': p.read_text()} for p in (DATA / 'records').glob('*.md')]
            return self.respond(data)
        if parsed.path == '/auth/login':
            conf = config()
            if not conf.get('app_id') or not conf.get('app_secret'):
                return self.respond('请先在本机页面配置 App ID 和 App Secret。', 400, 'text/plain; charset=utf-8')
            state = secrets.token_urlsafe(32)
            nonce = secrets.token_urlsafe(32)
            OAUTH[state] = (time.time() + 600, nonce)
            return self.respond('', 302, headers={'Location': authorization_url(conf, state),
                    'Set-Cookie': 'oauth_nonce=' + nonce + '; HttpOnly; SameSite=Lax; Path=/auth; Max-Age=600'})
        if parsed.path == '/auth/callback':
            query = parse_qs(parsed.query)
            saved = OAUTH.pop(query.get('state', [''])[0], None)
            cookies = SimpleCookie(self.headers.get('Cookie', ''))
            nonce = cookies.get('oauth_nonce')
            if not saved or saved[0] < time.time() or not nonce or not secrets.compare_digest(saved[1], nonce.value):
                return self.respond('登录校验失败，请重新登录。', 403, 'text/plain; charset=utf-8')
            try:
                user = exchange_user(config(), query.get('code', [''])[0])
                conf = config()
                if conf.get('receive_open_id') and conf['receive_open_id'] != user['open_id']:
                    return self.respond('当前飞书账号与已绑定接收人不同。', 403, 'text/plain; charset=utf-8')
                save_config({'receive_open_id': user['open_id']})
                session = secrets.token_urlsafe(32)
                SESSIONS[session] = {'user': user, 'expires': time.time() + 86400}
            except Exception:
                return self.respond('飞书登录失败，请检查应用回调地址、权限及可用范围。', 400, 'text/plain; charset=utf-8')
            return self.respond('', 302, headers={'Location': '/', 'Set-Cookie': 'session=' + session + '; HttpOnly; SameSite=Lax; Path=/; Max-Age=86400'})
        return self.respond({'error': '不存在'}, 404)

    def do_POST(self):
        if not self.host_ok() or self.headers.get('Origin') != ORIGIN or self.headers.get('X-CSRF-Token') != CSRF:
            return self.respond({'error': '请求校验失败'}, 403)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > 20000:
                raise ValueError()
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError()
        except (ValueError, json.JSONDecodeError):
            return self.respond({'error': '无效请求'}, 400)
        if self.path == '/api/config':
            try:
                save_config(body)
            except ValueError:
                return self.respond({'error': '连接格式不正确，请检查完整的 Webhook、Bot Token 或 Chat ID'}, 400)
            with connect() as db:
                db.execute("UPDATE events SET next_attempt=0 WHERE status='pending'")
                db.execute("UPDATE deliveries SET next_attempt=0 WHERE status='pending'")
            return self.respond({'detail': '配置已保存在本机，可发送测试消息验证'})
        if self.path == '/api/test':
            requested = body.get('channel')
            available = active_channels(config())
            if not available or requested and requested not in available:
                return self.respond({'error': '请先配置并启用要测试的推送通道'}, 400)
            enqueue({'id': 'test:' + secrets.token_hex(8), 'kind': 'test',
                     'channels': [requested] if requested else available,
                     'text': 'Fomo 监测 · 推送连接测试。此消息仅用于验证连接，不是行情信号。',
                     'observed_at': time.strftime('%Y-%m-%d %H:%M:%S %z')})
            return self.respond({'detail': '测试消息已入队，请在推送记录中查看发送结果'})
        if self.path == '/api/telegram/start':
            conf = config()
            if not conf.get('telegram_token'):
                return self.respond({'error':'请先填写 Bot Token 并点击“保存连接设置”，再开始 Telegram 登录'},400)
            try:
                bot = telegram_client.get_bot(conf)
            except Exception:
                return self.respond({'error':'Telegram 连接失败，请检查 Bot Token 和网络'},400)
            nonce = secrets.token_urlsafe(24)
            ticket = secrets.token_urlsafe(32)
            now = time.time()
            with TG_LOCK:
                for key in list(TG_BINDINGS):
                    if TG_BINDINGS[key]['issued_at'] < now-600:
                        TG_BINDINGS.pop(key,None)
                TG_BINDINGS[ticket] = {'nonce':nonce,'issued_at':now,'token_hash':hashlib.sha256(conf['telegram_token'].encode()).hexdigest()}
            return self.respond({'detail':'请打开机器人并点击 Start，然后回到此处完成验证',
                                 'url':'https://t.me/'+bot['username']+'?start='+nonce},
                                headers={'Set-Cookie':'tg_binding='+ticket+'; HttpOnly; SameSite=Strict; Path=/api/telegram; Max-Age=600'})
        if self.path == '/api/telegram/finish':
            cookie = SimpleCookie(self.headers.get('Cookie','')).get('tg_binding')
            ticket = cookie.value if cookie else ''
            with TG_LOCK:
                binding = TG_BINDINGS.pop(ticket,None)
            conf = config()
            if not binding or binding['issued_at'] < time.time()-600 or binding['token_hash'] != hashlib.sha256(conf.get('telegram_token','').encode()).hexdigest():
                return self.respond({'error':'绑定请求已过期或机器人已变更，请重新开始'},400)
            try:
                user = telegram_client.find_binding(conf,binding['nonce'],binding['issued_at'])
            except Exception:
                with TG_LOCK:
                    TG_BINDINGS[ticket] = binding
                return self.respond({'error':'无法读取验证消息；请检查网络。若机器人已有 Webhook 或被其他程序轮询，请使用 Chat ID 手动连接'},400)
            if not user:
                with TG_LOCK:
                    TG_BINDINGS[ticket] = binding
                return self.respond({'error':'尚未收到验证消息，请在机器人私聊中点击 Start，再点击完成验证'},400)
            try:
                save_config({'telegram_chat_id':str(user['chat_id'])}, expected_telegram_token=conf['telegram_token'])
            except ValueError:
                return self.respond({'error':'机器人已变更，请重新开始绑定'},400)
            session = secrets.token_urlsafe(32)
            SESSIONS[session] = {'user':{'name':user.get('name') or user.get('username') or str(user['id']),'provider':'Telegram'},'expires':time.time()+86400}
            return self.respond({'detail':'Telegram 身份已验证，个人推送已绑定'},
                                headers={'Set-Cookie':'session='+session+'; HttpOnly; SameSite=Lax; Path=/; Max-Age=86400'})
        if self.path == '/api/refresh':
            return self.respond(request_refresh())
        if self.path == '/api/update/check':
            try:
                result = update_checker.check(ROOT)
                state_set('update', result)
                return self.respond(result)
            except Exception:
                return self.respond({'error':'暂时无法连接 GitHub 检查更新'}, 502)
        return self.respond({'error': '不存在'}, 404)

if __name__ == '__main__':
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.umask(0o077)
    lock = open(DATA / 'server.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    connect().close()
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=update_worker, daemon=True).start()
    ThreadingHTTPServer(('127.0.0.1', 8765), Handler).serve_forever()
