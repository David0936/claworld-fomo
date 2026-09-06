"""Small, dependency-free Feishu notification and OAuth client.

The module intentionally keeps the public surface small because it is used by
the local monitor process:

``send_text(config, text)``
    Send a text notification through either a Feishu custom-bot webhook or a
    self-built app bot.

``authorization_url(config, state)`` and ``exchange_user(config, code)``
    Implement the Feishu web OAuth flow without returning access tokens to the
    caller.

Only the Python standard library is used.  In particular, the HTTP client
rejects redirects so a webhook token or app credential cannot be forwarded to
an unexpected host.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import threading
import time
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


API_BASE = "https://open.feishu.cn"
OAUTH_BASE = "https://accounts.feishu.cn"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/auth/callback"
DEFAULT_TIMEOUT = 10.0

_WEBHOOK_PATH_RE = re.compile(r"^/open-apis/bot/v2/hook/[A-Za-z0-9._~-]+$")
_CODE_RE = re.compile(r"^[+-]?\d+$")


class FeishuError(RuntimeError):
    """Raised when a Feishu request or response cannot be completed safely."""


class _RedirectRejected(Exception):
    """Internal marker used by the no-redirect HTTP handler."""


class _NoRedirectHandler(HTTPRedirectHandler):
    """Prevent urllib from following a 3xx response."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise _RedirectRejected()


_CACHE_LOCK = threading.RLock()
_APP_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_TENANT_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


def _require_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(config, Mapping):
        raise TypeError("Feishu config must be a mapping")
    return config


def _required_string(config: Mapping[str, Any], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError("Feishu configuration is missing a required value")
    return value


def _timeout(config: Mapping[str, Any]) -> float:
    value = config.get("timeout", DEFAULT_TIMEOUT)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError("Feishu timeout must be a positive number")
    return float(value)


def _validate_webhook_url(webhook: str) -> None:
    """Accept only the exact Feishu custom-bot webhook URL shape."""

    if not isinstance(webhook, str) or not webhook:
        raise ValueError("Invalid Feishu webhook URL")
    try:
        parts = urlsplit(webhook)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        raise ValueError("Invalid Feishu webhook URL") from None

    if (
        parts.scheme != "https"
        or hostname != "open.feishu.cn"
        or port is not None
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or not _WEBHOOK_PATH_RE.fullmatch(parts.path)
    ):
        raise ValueError("Invalid Feishu webhook URL")


def _validate_redirect_uri(redirect_uri: str) -> None:
    """Validate an OAuth callback URL before placing it in an auth URL."""

    if not isinstance(redirect_uri, str) or not redirect_uri:
        raise ValueError("Invalid Feishu redirect URI")
    try:
        parts = urlsplit(redirect_uri)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        raise ValueError("Invalid Feishu redirect URI") from None

    if (
        parts.scheme not in {"http", "https"}
        or not hostname
        or port is None and parts.netloc.endswith(":")
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise ValueError("Invalid Feishu redirect URI")


def _signature(timestamp: int | str, secret: str) -> str:
    """Create the custom-bot signature required by Feishu.

    Feishu's custom bot example uses ``timestamp + "\\n" + secret`` as the
    HMAC key and an empty message.  This is intentionally different from the
    more common ``secret`` key / string-to-sign message arrangement.
    """

    if not isinstance(secret, str) or not secret:
        raise ValueError("Invalid Feishu signing secret")
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _safe_code(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and _CODE_RE.fullmatch(value):
        return value
    return "unknown"


def _is_zero(value: Any) -> bool:
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value == 0
    return isinstance(value, str) and value.strip() in {"0", "+0", "-0"}


def _check_provider_response(result: Any, operation: str) -> Mapping[str, Any]:
    if not isinstance(result, Mapping):
        raise FeishuError(f"Feishu {operation} returned an invalid response")

    code_key = None
    if "code" in result:
        code_key = "code"
    elif "StatusCode" in result:
        code_key = "StatusCode"
    if code_key is not None and not _is_zero(result.get(code_key)):
        code = _safe_code(result.get(code_key))
        raise FeishuError(f"Feishu {operation} failed (provider code {code})")
    return result


def _open_request(request: Request, timeout: float):
    """Open a request with redirects disabled.

    This helper is kept separate so tests can replace it with a deterministic
    response without ever making a real network request.
    """

    opener = build_opener(_NoRedirectHandler())
    return opener.open(request, timeout=timeout)


def _request_json(
    url: str,
    payload: Mapping[str, Any] | None,
    timeout: float,
    headers: Mapping[str, str] | None = None,
    operation: str = "request",
    method: str = "POST",
) -> Mapping[str, Any]:
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "fomo-monitor/1.0",
    }
    if headers:
        request_headers.update(headers)
    encoded_payload = None
    if payload is not None:
        try:
            encoded_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        except (TypeError, ValueError):
            raise FeishuError(f"Feishu {operation} request could not be encoded") from None

    request = Request(url, data=encoded_payload, headers=request_headers, method=method)
    try:
        response = _open_request(request, timeout)
        try:
            status = getattr(response, "status", getattr(response, "code", 200))
            response_url = response.geturl() if hasattr(response, "geturl") else url
            if isinstance(status, int) and 300 <= status < 400:
                raise FeishuError(f"Feishu {operation} redirected (not allowed)")
            if response_url and response_url != url:
                raise FeishuError(f"Feishu {operation} redirected (not allowed)")
            body = response.read()
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
    except FeishuError:
        raise
    except _RedirectRejected:
        raise FeishuError(f"Feishu {operation} redirected (not allowed)") from None
    except HTTPError as exc:
        if 300 <= exc.code < 400:
            raise FeishuError(f"Feishu {operation} redirected (not allowed)") from None
        raise FeishuError(f"Feishu {operation} failed with HTTP status {exc.code}") from None
    except (TimeoutError, URLError, OSError):
        raise FeishuError(f"Feishu {operation} network request failed") from None

    if isinstance(body, bytes):
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            raise FeishuError(f"Feishu {operation} returned invalid JSON") from None
    elif isinstance(body, str):
        text = body
    else:
        raise FeishuError(f"Feishu {operation} returned invalid JSON")
    if not text.strip():
        raise FeishuError(f"Feishu {operation} returned an empty response")
    try:
        result = json.loads(text)
    except (TypeError, ValueError):
        raise FeishuError(f"Feishu {operation} returned invalid JSON") from None
    checked = _check_provider_response(result, operation)
    return dict(checked)


def _credential_cache_key(app_id: str, app_secret: str) -> str:
    # Avoid retaining the raw app secret as a dictionary key in process memory.
    raw = f"{app_id}\0{app_secret}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _cached_token(cache: dict[str, tuple[str, float]], key: str) -> str | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        item = cache.get(key)
        if item is None:
            return None
        token, expires_at = item
        if expires_at <= now:
            cache.pop(key, None)
            return None
        return token


def _save_token(
    cache: dict[str, tuple[str, float]], key: str, token: str, expire: Any
) -> None:
    try:
        lifetime = float(expire)
    except (TypeError, ValueError):
        lifetime = 0.0
    if lifetime <= 0:
        return
    # A short margin avoids using a token while it is expiring at the provider.
    lifetime = max(0.0, lifetime - 30.0)
    with _CACHE_LOCK:
        cache[key] = (token, time.monotonic() + lifetime)


def _response_value(result: Mapping[str, Any], key: str) -> Any:
    if key in result:
        return result.get(key)
    data = result.get("data")
    if isinstance(data, Mapping):
        return data.get(key)
    return None


def _require_success_code(result: Mapping[str, Any], operation: str) -> None:
    """Require a provider success marker before reporting a send as complete."""

    if "code" in result:
        key = "code"
    elif "StatusCode" in result:
        key = "StatusCode"
    else:
        raise FeishuError(f"Feishu {operation} returned no success code")
    if not _is_zero(result.get(key)):
        # Normally handled by _request_json; keep this guard for callers that
        # replace the HTTP helper in tests or embed it in another process.
        raise FeishuError(f"Feishu {operation} did not report success")


def _get_app_access_token(config: Mapping[str, Any]) -> str:
    app_id = _required_string(config, "app_id")
    app_secret = _required_string(config, "app_secret")
    key = _credential_cache_key(app_id, app_secret)
    cached = _cached_token(_APP_TOKEN_CACHE, key)
    if cached:
        return cached

    result = _request_json(
        f"{API_BASE}/open-apis/auth/v3/app_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret},
        _timeout(config),
        operation="app token request",
    )
    token = _response_value(result, "app_access_token")
    if not isinstance(token, str) or not token:
        raise FeishuError("Feishu app token response did not include a token")
    _save_token(_APP_TOKEN_CACHE, key, token, _response_value(result, "expire"))
    return token


def _get_tenant_access_token(config: Mapping[str, Any]) -> str:
    app_id = _required_string(config, "app_id")
    app_secret = _required_string(config, "app_secret")
    key = _credential_cache_key(app_id, app_secret)
    cached = _cached_token(_TENANT_TOKEN_CACHE, key)
    if cached:
        return cached

    result = _request_json(
        f"{API_BASE}/open-apis/auth/v3/tenant_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret},
        _timeout(config),
        operation="tenant token request",
    )
    token = _response_value(result, "tenant_access_token")
    if not isinstance(token, str) or not token:
        raise FeishuError("Feishu tenant token response did not include a token")
    _save_token(_TENANT_TOKEN_CACHE, key, token, _response_value(result, "expire"))
    return token


def _send_webhook(config: Mapping[str, Any], text: str) -> str:
    webhook = config.get("webhook")
    _validate_webhook_url(webhook)

    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    signing_secret = config.get("signing_secret")
    if signing_secret:
        timestamp = int(time.time())
        payload["timestamp"] = timestamp
        payload["sign"] = _signature(timestamp, signing_secret)
    elif signing_secret is not None and signing_secret != "":
        raise ValueError("Invalid Feishu signing secret")

    result = _request_json(
        webhook,
        payload,
        _timeout(config),
        operation="webhook request",
    )
    _require_success_code(result, "webhook request")
    message_id = result.get("message_id")
    if isinstance(message_id, str) and message_id:
        return message_id
    data = result.get("data")
    if isinstance(data, Mapping):
        message_id = data.get("message_id")
        if isinstance(message_id, str) and message_id:
            return message_id
    return "success"


def _send_app_bot(config: Mapping[str, Any], text: str) -> str:
    receive_open_id = _required_string(config, "receive_open_id")
    tenant_access_token = _get_tenant_access_token(config)
    result = _request_json(
        f"{API_BASE}/open-apis/im/v1/messages?receive_id_type=open_id",
        {
            "receive_id": receive_open_id,
            "msg_type": "text",
            # Feishu's server message API expects content as a JSON string.
            "content": json.dumps({"text": text}, ensure_ascii=False, separators=(",", ":")),
        },
        _timeout(config),
        headers={"Authorization": f"Bearer {tenant_access_token}"},
        operation="message request",
    )
    _require_success_code(result, "message request")
    message_id = _response_value(result, "message_id")
    if isinstance(message_id, str) and message_id:
        return message_id
    return "success"


def send_text(config: Mapping[str, Any], text: str) -> str:
    """Send ``text`` and return the provider message id or ``"success"``.

    A configured ``webhook`` always takes precedence.  Without one, the
    configuration must contain ``app_id``, ``app_secret`` and
    ``receive_open_id`` for an app bot personal push.
    """

    config = _require_config(config)
    if not isinstance(text, str):
        raise TypeError("Feishu text must be a string")
    webhook = config.get("webhook")
    if webhook:
        return _send_webhook(config, text)
    return _send_app_bot(config, text)


def authorization_url(config: Mapping[str, Any], state: str) -> str:
    """Return the Feishu OAuth authorization URL for a callback state."""

    config = _require_config(config)
    app_id = _required_string(config, "app_id")
    if not isinstance(state, str) or not state:
        raise ValueError("Feishu OAuth state must be a non-empty string")
    redirect_uri = config.get("redirect_uri", DEFAULT_REDIRECT_URI)
    _validate_redirect_uri(redirect_uri)
    params = {
        "client_id": app_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    scope = config.get("scope")
    if scope:
        if not isinstance(scope, str):
            raise ValueError("Invalid Feishu OAuth scope")
        params["scope"] = scope
    return f"{OAUTH_BASE}/open-apis/authen/v1/authorize?{urlencode(params)}"


def exchange_user(config: Mapping[str, Any], code: str) -> dict[str, Any]:
    """Exchange an OAuth code and return only the public user identity fields."""

    config = _require_config(config)
    if not isinstance(code, str) or not code:
        raise ValueError("Feishu OAuth code must be a non-empty string")
    app_id = _required_string(config, "app_id")
    app_secret = _required_string(config, "app_secret")
    redirect_uri = config.get("redirect_uri", DEFAULT_REDIRECT_URI)
    _validate_redirect_uri(redirect_uri)
    token_payload: dict[str, Any] = {
        "grant_type": "authorization_code",
        "client_id": app_id,
        "client_secret": app_secret,
        "code": code,
        "redirect_uri": redirect_uri,
    }
    scope = config.get("scope")
    if scope:
        if not isinstance(scope, str):
            raise ValueError("Invalid Feishu OAuth scope")
        token_payload["scope"] = scope
    token_result = _request_json(
        f"{API_BASE}/open-apis/authen/v2/oauth/token",
        token_payload,
        _timeout(config),
        operation="OAuth token request",
    )
    _require_success_code(token_result, "OAuth token request")
    access_token = _response_value(token_result, "access_token")
    if not isinstance(access_token, str) or not access_token:
        raise FeishuError("Feishu OAuth response did not include an access token")

    user_result = _request_json(
        f"{API_BASE}/open-apis/authen/v1/user_info",
        None,
        _timeout(config),
        headers={"Authorization": f"Bearer {access_token}"},
        operation="OAuth user info request",
        method="GET",
    )
    _require_success_code(user_result, "OAuth user info request")
    data = user_result.get("data")
    if not isinstance(data, Mapping):
        raise FeishuError("Feishu OAuth user info response did not include user data")
    open_id = data.get("open_id")
    if not isinstance(open_id, str) or not open_id:
        raise FeishuError("Feishu OAuth response did not include a user id")
    return {
        "open_id": open_id,
        "name": data.get("name"),
        "avatar_url": data.get("avatar_url"),
    }


def _clear_token_caches() -> None:
    """Clear in-memory token caches; useful for process lifecycle and tests."""

    with _CACHE_LOCK:
        _APP_TOKEN_CACHE.clear()
        _TENANT_TOKEN_CACHE.clear()


__all__ = [
    "DEFAULT_REDIRECT_URI",
    "FeishuError",
    "authorization_url",
    "exchange_user",
    "send_text",
]
