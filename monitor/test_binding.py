import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import server

class BindingTests(unittest.TestCase):
    def setUp(self):
        self.conf={'telegram_token':'123456:'+('a'*35)}
        self.config_patch=patch.object(server,'config',side_effect=lambda:dict(self.conf))
        self.config_patch.start()
        self.app=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.thread=threading.Thread(target=self.app.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.app.shutdown();self.app.server_close();self.config_patch.stop()

    def post(self,path,cookie=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.app.server_port)
        headers={'Host':'127.0.0.1:8765','Origin':server.ORIGIN,'X-CSRF-Token':server.CSRF,'Content-Type':'application/json'}
        if cookie:headers['Cookie']=cookie
        conn.request('POST',path,body='{}',headers=headers)
        r=conn.getresponse();data=json.loads(r.read());cookies=r.getheader('Set-Cookie');code=r.status
        conn.close();return code,data,cookies

    def start(self):
        with patch.object(server.telegram_client,'get_bot',return_value={'id':123,'username':'TestBot'}):
            code,data,cookie=self.post('/api/telegram/start')
        self.assertEqual(code,200)
        self.assertTrue(data['url'].startswith('https://t.me/TestBot?start='))
        return cookie

    def test_binding_requires_same_browser_ticket_and_is_single_use(self):
        cookie=self.start()
        with patch.object(server.telegram_client,'find_binding',return_value={'id':456,'chat_id':456,'name':'Test'}) as find,patch.object(server,'save_config') as save:
            self.assertEqual(self.post('/api/telegram/finish')[0],400)
            find.assert_not_called()
            code,data,session=self.post('/api/telegram/finish',cookie)
            self.assertEqual(code,200)
            save.assert_called_once_with({'telegram_chat_id':'456'},expected_telegram_token=self.conf['telegram_token'])
            self.assertIn('HttpOnly',session)
            self.assertEqual(self.post('/api/telegram/finish',cookie)[0],400)

    def test_changed_token_invalidates_binding(self):
        cookie=self.start();self.conf['telegram_token']='changed'
        with patch.object(server.telegram_client,'find_binding') as find:
            self.assertEqual(self.post('/api/telegram/finish',cookie)[0],400)
            find.assert_not_called()

    def test_no_message_can_retry(self):
        cookie=self.start()
        with patch.object(server.telegram_client,'find_binding',return_value=None):
            self.assertIn('尚未收到',self.post('/api/telegram/finish',cookie)[1]['error'])
            self.assertIn('尚未收到',self.post('/api/telegram/finish',cookie)[1]['error'])
