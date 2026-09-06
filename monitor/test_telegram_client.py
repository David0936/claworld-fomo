import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

try:
    from monitor import telegram_client
except ImportError:  # unittest discovery launched from inside monitor/
    import telegram_client


TOKEN = "123456:ABC_def-1234567890"
CONFIG = {"telegram_token": TOKEN, "telegram_chat_id": "42"}


class _Response:
    def __init__(self, body, status=200, url=None):
        self._body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.status = status
        self._url = url
        self.closed = False

    def read(self):
        return self._body

    def geturl(self):
        return self._url

    def close(self):
        self.closed = True


def _response(value, status=200, url=None):
    return _Response(json.dumps(value), status=status, url=url)


class TelegramClientTests(unittest.TestCase):
    def test_send_text_uses_configured_chat_and_splits_at_telegram_limit(self):
        opened = []

        def open_request(request, timeout):
            opened.append((request, timeout))
            return _response({"ok": True, "result": {"message_id": len(opened)}})

        text = "a" * 4096 + "中" * 4096 + "tail"
        with patch.object(telegram_client, "_open_request", side_effect=open_request):
            self.assertEqual(telegram_client.send_text(CONFIG, text), 3)

        self.assertEqual(len(opened), 3)
        self.assertTrue(all(timeout == 10.0 for _, timeout in opened))
        payloads = [json.loads(request.data.decode("utf-8")) for request, _ in opened]
        self.assertEqual([len(payload["text"]) for payload in payloads], [4096, 4096, 4])
        self.assertEqual([payload["chat_id"] for payload in payloads], ["42"] * 3)
        self.assertEqual([payload["text"] for payload in payloads], [
            "a" * 4096,
            "中" * 4096,
            "tail",
        ])
        self.assertEqual(urlsplit(opened[0][0].full_url).netloc, "api.telegram.org")

    def test_send_requires_expected_message_id_and_does_not_leak_token(self):
        with patch.object(
            telegram_client,
            "_open_request",
            return_value=_response({"ok": False, "error_code": 401, "description": TOKEN}),
        ):
            with self.assertRaises(telegram_client.TelegramError) as raised:
                telegram_client.send_text(CONFIG, "hello")
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assertIn("401", str(raised.exception))

    def test_get_bot_validates_token_and_returns_public_identity(self):
        opened = []

        def open_request(request, timeout):
            opened.append((request, timeout))
            return _response({
                "ok": True,
                "result": {"id": 123, "is_bot": True, "first_name": "Fomo", "username": "fomo_bot"},
            })

        with patch.object(telegram_client, "_open_request", side_effect=open_request):
            self.assertEqual(telegram_client.get_bot(CONFIG), {"id": 123, "username": "fomo_bot"})
        self.assertEqual(opened[0][0].get_method(), "POST")
        self.assertEqual(opened[0][1], 10.0)
        self.assertEqual(opened[0][0].full_url, f"https://api.telegram.org/bot{TOKEN}/getMe")

    def test_invalid_token_shape_is_rejected_before_network(self):
        with patch.object(telegram_client, "_open_request") as opened:
            for bad in ("not-a-token", "123:abc/def", "123:abc def", "123:abc:extra"):
                with self.subTest(token=bad):
                    with self.assertRaises(ValueError):
                        telegram_client.get_bot({"telegram_token": bad})
        opened.assert_not_called()

    def test_redirects_are_rejected_and_url_is_not_exposed(self):
        with patch.object(telegram_client, "_open_request", side_effect=telegram_client._RedirectRejected()):
            with self.assertRaisesRegex(telegram_client.TelegramError, "redirected") as raised:
                telegram_client.get_bot(CONFIG)
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assertNotIn("api.telegram.org", str(raised.exception))

        redirect_url = "https://evil.example/collect?token=" + TOKEN
        response = _Response("{}", status=302, url=redirect_url)
        with patch.object(telegram_client, "_open_request", return_value=response):
            with self.assertRaisesRegex(telegram_client.TelegramError, "redirected") as raised:
                telegram_client.get_bot(CONFIG)
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assertNotIn(redirect_url, str(raised.exception))

    def test_find_binding_matches_exact_recent_private_message(self):
        issued_at = 1_700_000_000
        responses = iter([
            _response({"ok": True, "result": {"url": ""}}),
            _response({"ok": True, "result": [{
                "update_id": 10,
                "message": {
                    "date": issued_at - 4,
                    "text": "/start nonce_123",
                    "chat": {"id": 77, "type": "private"},
                    "from": {"id": 77, "first_name": "Alice", "last_name": "Ng", "username": "alice"},
                },
            }]})
        ])
        opened = []

        def open_request(request, timeout):
            opened.append((request, timeout))
            return next(responses)

        with patch.object(telegram_client, "_open_request", side_effect=open_request):
            binding = telegram_client.find_binding(CONFIG, "nonce_123", issued_at)
        self.assertEqual(binding, {
            "id": 77,
            "name": "Alice Ng",
            "username": "alice",
            "chat_id": 77,
        })
        self.assertEqual(len(opened), 2)
        self.assertEqual(urlsplit(opened[1][0].full_url).netloc, "api.telegram.org")
        self.assertEqual(parse_qs(urlsplit(opened[1][0].full_url).query), {"timeout": ["0"]})
        self.assertEqual(opened[1][0].get_method(), "GET")
        self.assertEqual(opened[1][1], 10.0)

    def test_find_binding_rejects_stale_group_sender_mismatch_and_non_exact_text(self):
        issued_at = 1_700_000_000
        updates = [
            {"message": {
                "date": issued_at - 6,
                "text": "/start nonce_123",
                "chat": {"id": 77, "type": "private"},
                "from": {"id": 77, "first_name": "Stale"},
            }},
            {"message": {
                "date": issued_at,
                "text": "/start nonce_123",
                "chat": {"id": -99, "type": "group"},
                "from": {"id": 77, "first_name": "Group"},
            }},
            {"message": {
                "date": issued_at,
                "text": "/start nonce_123 extra",
                "chat": {"id": 77, "type": "private"},
                "from": {"id": 77, "first_name": "Extra"},
            }},
            {"message": {
                "date": issued_at,
                "text": "/start nonce_123",
                "chat": {"id": 77, "type": "private"},
                "from": {"id": 78, "first_name": "Mismatch"},
            }},
        ]
        responses = iter([
            _response({"ok": True, "result": {"url": ""}}),
            _response({"ok": True, "result": updates}),
        ])
        with patch.object(telegram_client, "_open_request", side_effect=lambda *_: next(responses)):
            self.assertIsNone(telegram_client.find_binding(CONFIG, "nonce_123", issued_at))

    def test_find_binding_blocks_active_webhook_without_deleting_it(self):
        webhook_url = "https://private.example/webhook/" + TOKEN
        responses = iter([_response({"ok": True, "result": {"url": webhook_url}})])
        opened = []

        def open_request(request, timeout):
            opened.append(request.full_url)
            return next(responses)

        with patch.object(telegram_client, "_open_request", side_effect=open_request):
            with self.assertRaisesRegex(telegram_client.TelegramError, "manual Chat ID binding") as raised:
                telegram_client.find_binding(CONFIG, "nonce_123", 1_700_000_000)
        self.assertEqual(len(opened), 1)
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assertNotIn(webhook_url, str(raised.exception))
        self.assertNotIn("deleteWebhook", " ".join(opened))

    def test_get_updates_does_not_send_offset_or_consume_updates(self):
        responses = iter([
            _response({"ok": True, "result": {"url": ""}}),
            _response({"ok": True, "result": []}),
        ])
        opened = []

        def open_request(request, timeout):
            opened.append(request)
            return next(responses)

        with patch.object(telegram_client, "_open_request", side_effect=open_request):
            self.assertIsNone(telegram_client.find_binding(CONFIG, "nonce_123", 1_700_000_000))
        updates_url = opened[1].full_url
        self.assertNotIn("offset", updates_url)
        self.assertNotIn("deleteWebhook", " ".join(request.full_url for request in opened))

    def test_ok_must_be_json_true(self):
        for value in (1, "true", None):
            with self.subTest(ok=value):
                with patch.object(
                    telegram_client,
                    "_open_request",
                    return_value=_response({"ok": value, "result": {"id": 1, "username": "bot"}}),
                ):
                    with self.assertRaises(telegram_client.TelegramError):
                        telegram_client.get_bot(CONFIG)


if __name__ == "__main__":
    unittest.main()
