import base64
import hashlib
import hmac
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

try:
    from monitor import feishu
except ImportError:  # unittest discovery launched from inside monitor/
    import feishu


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


class FeishuTests(unittest.TestCase):
    WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/test-token"

    def setUp(self):
        feishu._clear_token_caches()

    def test_webhook_payload_signature_and_timeout(self):
        response = _Response('{"StatusCode":0,"StatusMessage":"success"}')
        with patch.object(feishu, "time") as mocked_time:
            mocked_time.time.return_value = 1700000000.9
            with patch.object(feishu, "_open_request", return_value=response) as opened:
                result = feishu.send_text(
                    {
                        "webhook": self.WEBHOOK,
                        "signing_secret": "sign-secret",
                        "timeout": 2.5,
                    },
                    "涨幅提醒 🚀",
                )

        self.assertEqual(result, "success")
        request, timeout = opened.call_args.args
        self.assertEqual(timeout, 2.5)
        self.assertEqual(request.full_url, self.WEBHOOK)
        self.assertEqual(request.get_method(), "POST")
        payload = json.loads(request.data.decode("utf-8"))
        expected_key = b"1700000000\nsign-secret"
        expected_sign = base64.b64encode(
            hmac.new(expected_key, digestmod=hashlib.sha256).digest()
        ).decode("ascii")
        self.assertEqual(payload, {
            "timestamp": 1700000000,
            "sign": expected_sign,
            "msg_type": "text",
            "content": {"text": "涨幅提醒 🚀"},
        })
        self.assertEqual(request.headers["Content-type"], "application/json; charset=utf-8")

    def test_webhook_provider_error_is_raised_without_provider_message(self):
        response = _Response('{"StatusCode":19001,"StatusMessage":"contains-secret-value"}')
        with patch.object(feishu, "_open_request", return_value=response):
            with self.assertRaises(feishu.FeishuError) as raised:
                feishu.send_text({"webhook": self.WEBHOOK}, "hello")
        self.assertIn("19001", str(raised.exception))
        self.assertNotIn("contains-secret-value", str(raised.exception))

    def test_webhook_url_restrictions(self):
        invalid_urls = (
            "http://open.feishu.cn/open-apis/bot/v2/hook/token",
            "https://evil.example/open-apis/bot/v2/hook/token",
            "https://open.feishu.cn/open-apis/bot/v2/hook/token?x=1",
            "https://open.feishu.cn/open-apis/bot/v2/hook/token#fragment",
            "https://user:pass@open.feishu.cn/open-apis/bot/v2/hook/token",
            "https://open.feishu.cn:443/open-apis/bot/v2/hook/token",
            "https://open.feishu.cn/open-apis/bot/v2/hook/token/extra",
            "https://open.feishu.cn/open-apis/bot/v1/hook/token",
        )
        with patch.object(feishu, "_open_request") as opened:
            for webhook in invalid_urls:
                with self.subTest(webhook=webhook):
                    with self.assertRaises(ValueError):
                        feishu.send_text({"webhook": webhook}, "hello")
        opened.assert_not_called()

    def test_redirects_are_rejected(self):
        with patch.object(feishu, "_open_request", side_effect=feishu._RedirectRejected()):
            with self.assertRaisesRegex(feishu.FeishuError, "redirected"):
                feishu.send_text({"webhook": self.WEBHOOK}, "hello")

    def test_app_bot_uses_cached_tenant_token_and_message_id(self):
        token_response = _Response(
            '{"code":0,"tenant_access_token":"tenant-token","expire":3600}'
        )
        message_response = _Response('{"code":0,"data":{"message_id":"om_123"}}')
        opened_responses = iter((token_response, message_response, message_response))

        def open_request(request, timeout):
            requests.append((request, timeout))
            return next(opened_responses)

        requests = []
        config = {
            "app_id": "cli_app",
            "app_secret": "app-secret",
            "receive_open_id": "ou_user",
            "timeout": 4,
        }
        with patch.object(feishu, "_open_request", side_effect=open_request):
            self.assertEqual(feishu.send_text(config, "hello"), "om_123")
            self.assertEqual(feishu.send_text(config, "again"), "om_123")

        self.assertEqual(len(requests), 3)
        token_request, token_timeout = requests[0]
        self.assertEqual(token_timeout, 4.0)
        self.assertEqual(token_request.full_url,
                         "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal")
        self.assertEqual(json.loads(token_request.data), {
            "app_id": "cli_app",
            "app_secret": "app-secret",
        })
        message_request, _ = requests[1]
        self.assertEqual(message_request.full_url,
                         "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id")
        self.assertEqual(message_request.headers["Authorization"], "Bearer tenant-token")
        body = json.loads(message_request.data)
        self.assertEqual(body["receive_id"], "ou_user")
        self.assertEqual(body["msg_type"], "text")
        self.assertEqual(json.loads(body["content"]), {"text": "hello"})

    def test_oauth_url_default_redirect_and_user_mapping(self):
        auth_url = feishu.authorization_url({"app_id": "cli_app"}, "state with spaces")
        parts = urlsplit(auth_url)
        self.assertEqual(parts.scheme, "https")
        self.assertEqual(parts.netloc, "accounts.feishu.cn")
        self.assertEqual(parts.path, "/open-apis/authen/v1/authorize")
        self.assertEqual(parse_qs(parts.query), {
            "client_id": ["cli_app"],
            "response_type": ["code"],
            "redirect_uri": [feishu.DEFAULT_REDIRECT_URI],
            "state": ["state with spaces"],
        })

        oauth_token_response = _Response(
            '{"code":0,"access_token":"user-secret","expires_in":7200}'
        )
        user_response = _Response(
            '{"code":0,"data":{"open_id":"ou_1",'
            '"name":"Alice","avatar_url":"https://example/avatar.png",'
            '"email":"private@example.com"}}'
        )
        requests = []

        def open_request(request, timeout):
            requests.append((request, timeout))
            return (oauth_token_response, user_response)[len(requests) - 1]

        with patch.object(feishu, "_open_request", side_effect=open_request):
            user = feishu.exchange_user(
                {"app_id": "cli_app", "app_secret": "app-secret"}, "oauth-code"
            )
        self.assertEqual(user, {
            "open_id": "ou_1",
            "name": "Alice",
            "avatar_url": "https://example/avatar.png",
        })
        self.assertNotIn("access_token", user)
        oauth_request = requests[0][0]
        self.assertEqual(oauth_request.full_url,
                         "https://open.feishu.cn/open-apis/authen/v2/oauth/token")
        self.assertNotIn("Authorization", oauth_request.headers)
        self.assertEqual(json.loads(oauth_request.data), {
            "grant_type": "authorization_code",
            "client_id": "cli_app",
            "client_secret": "app-secret",
            "code": "oauth-code",
            "redirect_uri": feishu.DEFAULT_REDIRECT_URI,
        })
        self.assertEqual(requests[1][0].full_url,
                         "https://open.feishu.cn/open-apis/authen/v1/user_info")
        self.assertEqual(requests[1][0].get_method(), "GET")
        self.assertEqual(requests[1][0].headers["Authorization"], "Bearer user-secret")

    def test_oauth_provider_error_is_raised(self):
        response = _Response('{"code":999,"msg":"app-secret leaked in provider text"}')
        with patch.object(feishu, "_open_request", return_value=response):
            with self.assertRaises(feishu.FeishuError) as raised:
                feishu.exchange_user(
                    {"app_id": "cli_app", "app_secret": "app-secret"}, "oauth-code"
                )
        self.assertIn("999", str(raised.exception))
        self.assertNotIn("app-secret", str(raised.exception))

    def test_send_requires_explicit_provider_success_code(self):
        response = _Response('{"msg":"success but no code"}')
        with patch.object(feishu, "_open_request", return_value=response):
            with self.assertRaisesRegex(feishu.FeishuError, "no success code"):
                feishu.send_text({"webhook": self.WEBHOOK}, "hello")

    def test_empty_send_response_is_rejected(self):
        response = _Response(b"")
        with patch.object(feishu, "_open_request", return_value=response):
            with self.assertRaisesRegex(feishu.FeishuError, "empty response"):
                feishu.send_text({"webhook": self.WEBHOOK}, "hello")


if __name__ == "__main__":
    unittest.main()
