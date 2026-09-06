import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import store

class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.override = patch.object(store, 'DATA', Path(self.tmp.name))
        self.override.start()

    def tearDown(self):
        self.override.stop()
        self.tmp.cleanup()

    def event(self, **extra):
        return dict({'id': 'hit:sol:abc', 'kind': 'hit', 'text': '测试信号', 'observed_at': '2026-09-06T12:00:00+08:00',
                     'source': 'windvane', 'verified': True, 'chain': 'sol', 'ca': 'a'*44,
                     'age_days': 2, 'market_cap': 300000, 'fomo_ratio': 17}, **extra)

    def test_hit_dedup_survives_reopen(self):
        self.assertTrue(store.enqueue(self.event()))
        self.assertFalse(store.enqueue(self.event()))
        self.assertEqual(store.snapshot()['counts'], {'pending': 1})

    def test_no_unverified_or_out_of_bounds_hit(self):
        for extra in [{'verified': False}, {'source': 'gmgn'}, {'fomo_ratio':14.5}, {'market_cap':500001},
                      {'age_days':0.9}, {'fomo_ratio':float('nan')}, {'ca':'short'}, {'fomo_ratio':True}]:
            with self.assertRaises(ValueError): store.enqueue(self.event(**extra))

    def test_boundaries(self):
        self.assertTrue(store.enqueue(self.event(age_days=1, market_cap=200000, fomo_ratio=15)))
        self.assertTrue(store.enqueue(self.event(id='second', age_days=7, market_cap=500000, fomo_ratio=20)))

    def test_state_roundtrip(self):
        store.state_set('scan', {'status':'partial','detail':'Base 登录过期'})
        self.assertEqual(store.snapshot()['scan']['status'], 'partial')

    def test_delivery_retry_and_recovery(self):
        import server
        store.enqueue(self.event())
        with patch.object(server, 'config', return_value={'webhook':'configured'}), patch.object(server, 'send_text', side_effect=RuntimeError('secret-url')):
            server.delivery_once()
        row = store.snapshot()['events'][0]
        self.assertEqual(row['status'], 'pending')
        self.assertEqual(row['attempts'], 1)
        self.assertNotIn('secret-url', row['error'])
        with store.connect() as db: db.execute('UPDATE deliveries SET next_attempt=0')
        with patch.object(server, 'config', return_value={'webhook':'configured'}), patch.object(server, 'send_text') as sender:
            server.delivery_once()
            server.delivery_once()
            self.assertEqual(sender.call_count, 1)
        self.assertEqual(store.snapshot()['events'][0]['status'], 'sent')

    def test_two_channels_retry_only_failed_destination(self):
        import server
        store.enqueue(self.event())
        conf={'webhook':'configured','telegram_token':'token','telegram_chat_id':'1'}
        with patch.object(server,'config',return_value=conf), patch.object(server,'send_text') as fs, patch.object(server.telegram_client,'send_text',side_effect=RuntimeError('private-token')) as tg:
            server.delivery_once()
            self.assertEqual(fs.call_count,1)
            self.assertEqual(tg.call_count,1)
        row=store.snapshot()['events'][0]
        self.assertEqual(row['status'],'pending')
        self.assertEqual({d['channel']:d['status'] for d in row['deliveries']},{'feishu':'sent','telegram':'pending'})
        with store.connect() as db: db.execute('UPDATE deliveries SET next_attempt=0')
        with patch.object(server,'config',return_value=conf), patch.object(server,'send_text') as fs, patch.object(server.telegram_client,'send_text') as tg:
            server.delivery_once()
            fs.assert_not_called()
            tg.assert_called_once()
        self.assertEqual(store.snapshot()['events'][0]['status'],'sent')

    def test_adding_channel_does_not_replay_old_events(self):
        import server
        store.enqueue(self.event())
        with patch.object(server,'config',return_value={'webhook':'configured'}),patch.object(server,'send_text'):
            server.delivery_once()
        with patch.object(server,'config',return_value={'webhook':'configured','telegram_token':'token','telegram_chat_id':'1'}), patch.object(server.telegram_client,'send_text') as tg:
            server.delivery_once()
            tg.assert_not_called()

    def test_channel_specific_test_message(self):
        import server
        store.enqueue(self.event(id='test',kind='test',channels=['telegram']))
        with patch.object(server,'config',return_value={'webhook':'configured','telegram_token':'token','telegram_chat_id':'1'}),patch.object(server,'send_text') as fs,patch.object(server.telegram_client,'send_text') as tg:
            server.delivery_once()
            fs.assert_not_called()
            tg.assert_called_once()

    def test_disabled_channel_waits_without_losing_pending_delivery(self):
        import server
        store.enqueue(self.event(channels=['telegram']))
        with patch.object(server,'config',return_value={'webhook':'configured','telegram_token':'token','telegram_chat_id':'1','telegram_enabled':False}),patch.object(server.telegram_client,'send_text') as tg:
            server.delivery_once()
            tg.assert_not_called()
        self.assertEqual(store.snapshot()['events'][0]['status'],'pending')

    def test_unconfigured_keeps_outbox(self):
        import server
        store.enqueue(self.event())
        with patch.object(server, 'config', return_value={}), patch.object(server, 'send_text') as sender:
            server.delivery_once()
            sender.assert_not_called()
        self.assertEqual(store.snapshot()['events'][0]['attempts'],0)

    def test_config_rejects_non_feishu_destination(self):
        import server
        with self.assertRaises(ValueError):
            server.save_config({'webhook':'http://127.0.0.1/private'})

    def test_refresh_subprocess_fixed_argv_and_cooldown(self):
        import server
        from types import SimpleNamespace
        with patch.object(server, 'thread_id', return_value='test-thread'), \
             patch.object(server.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            first = server.request_refresh()
            second = server.request_refresh()
        self.assertEqual(run.call_count, 1)
        self.assertIn('队列', first['detail'])
        self.assertIn('已有', second['detail'])
        self.assertEqual(run.call_args[0][0][1], 'queue')
        self.assertNotIn('shell', run.call_args[1])

if __name__ == '__main__': unittest.main()
