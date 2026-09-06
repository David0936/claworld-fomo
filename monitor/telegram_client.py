"""Small, dependency-free Telegram Bot API client.

The local monitor uses a bot for two related jobs: sending notifications to a
configured chat and binding the private Telegram account that started a local
login challenge.  This module deliberately talks only to Telegram's fixed TLS
endpoint and never follows redirects.  It also does not acknowledge updates
or remove a webhook, because either action could affect another user of the
bot.

Configuration keys are ``telegram_token`` and ``telegram_chat_id``.  A bot
token is kept out of exception messages.  ``send_text`` sends sequential
chunks when a message is longer than Telegram's 4096-character limit; if a
later chunk fails, earlier chunks may already have been delivered.
"""

from __future__ import annotations

import hmac
import json
import re
from collections.abc import Mapping
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


API_BASE = "https://api.telegram.org"
NETWORK_TIMEOUT = 10.0
MAX_TEXT_LENGTH = 4096

# Telegram documents bot tokens as a numeric bot id, a colon, and an opaque
# token made from letters, digits, ``_`` and ``-``.  Keep this shape check
# strict enough to prevent URL/path injection while allowing test and future
# token lengths accepted by the provider.
_TOKEN_RE = re.compile(r"^[0-9]{1,20}:[A-Za-z0-9_-]{1,256}$")


class TelegramError(RuntimeError):
    """Raised when a Telegram request or response cannot be used safely."""


class _RedirectRejected(Exception):
    """Internal marker raised when urllib sees an HTTP redirect."""


class _NoRedirectHandler(HTTPRedirectHandler):
    """Prevent urllib from following a 3xx response."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise _RedirectRejected()


def _require_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(config, Mapping):
        raise TypeError("Telegram config must be a mapping")
    return config


def _token(config: Mapping[str, Any]) -> str:
    value = config.get("telegram_token")
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise ValueError("Invalid Telegram bot token")
    return value


def _chat_id(config: Mapping[str, Any]) -> Any:
    value = config.get("telegram_chat_id")
    if isinstance(value, bool):
        raise ValueError("Invalid Telegram chat id")
    if isinstance(value, int):
        if value == 0:
            raise ValueError("Invalid Telegram chat id")
        return value
    if isinstance(value, str) and value.strip():
        # Preserve the configured text because Telegram accepts both numeric
        # ids and @channel usernames.  Whitespace around a value is harmless
        # configuration input, but whitespace within it is not.
        value = value.strip()
        if any(ch.isspace() for ch in value):
            raise ValueError("Invalid Telegram chat id")
        return value
    raise ValueError("Invalid Telegram chat id")


def _safe_provider_code(value: Any) -> str:
    """Return a harmless representation of Telegram's numeric error code."""

    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,6}", value):
        return value
    return "unknown"


def _request_url(token: str, method: str) -> str:
    # ``token`` has already passed _TOKEN_RE, so this cannot change the host,
    # scheme, or path used by the client.
    return f"{API_BASE}/bot{token}/{method}"


def _open_request(request: Request, timeout: float):
    """Open a request with redirects disabled.

    Tests replace this helper with a deterministic response.  Keeping the
    network boundary in one function also makes it difficult to accidentally
    introduce a redirect-following code path.
    """

    opener = build_opener(_NoRedirectHandler())
    return opener.open(request, timeout=timeout)


def _request_json(
    token: str,
    method: str,
    *,
    payload: Optional[Mapping[str, Any]] = None,
    query: Optional[Mapping[str, Any]] = None,
    operation: str,
    http_method: str = "POST",
) -> Mapping[str, Any]:
    """Call one Telegram method and require a JSON ``ok: true`` response."""

    url = _request_url(token, method)
    if query:
        url += "?" + urlencode(query)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "fomo-monitor/1.0",
    }
    encoded_payload = None
    if payload is not None:
        try:
            encoded_payload = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise TelegramError(f"Telegram {operation} request could not be encoded") from None

    request = Request(url, data=encoded_payload, headers=headers, method=http_method)
    try:
        response = _open_request(request, NETWORK_TIMEOUT)
        try:
            status = getattr(response, "status", getattr(response, "code", 200))
            response_url = response.geturl() if hasattr(response, "geturl") else url
            if isinstance(status, int) and 300 <= status < 400:
                raise TelegramError(f"Telegram {operation} redirected (not allowed)")
            if response_url and response_url != url:
                raise TelegramError(f"Telegram {operation} redirected (not allowed)")
            body = response.read()
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
    except TelegramError:
        raise
    except _RedirectRejected:
        raise TelegramError(f"Telegram {operation} redirected (not allowed)") from None
    except HTTPError as exc:
        if 300 <= exc.code < 400:
            raise TelegramError(f"Telegram {operation} redirected (not allowed)") from None
        raise TelegramError(
            f"Telegram {operation} failed with HTTP status {exc.code}"
        ) from None
    except (TimeoutError, URLError, OSError):
        raise TelegramError(f"Telegram {operation} network request failed") from None

    if isinstance(body, bytes):
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            raise TelegramError(f"Telegram {operation} returned invalid JSON") from None
    elif isinstance(body, str):
        text = body
    else:
        raise TelegramError(f"Telegram {operation} returned invalid JSON")
    if not text.strip():
        raise TelegramError(f"Telegram {operation} returned an empty response")
    try:
        result = json.loads(text)
    except (TypeError, ValueError):
        raise TelegramError(f"Telegram {operation} returned invalid JSON") from None
    if not isinstance(result, Mapping):
        raise TelegramError(f"Telegram {operation} returned an invalid response")

    # ``bool`` is intentionally checked by type: Python considers 1 equal to
    # True, but Telegram's success marker must be a JSON boolean.
    if type(result.get("ok")) is not bool:
        raise TelegramError(f"Telegram {operation} returned no valid success marker")
    if result.get("ok") is not True:
        code = _safe_provider_code(result.get("error_code"))
        raise TelegramError(f"Telegram {operation} failed (provider error {code})")
    return dict(result)


def _result(result: Mapping[str, Any], operation: str) -> Any:
    if "result" not in result:
        raise TelegramError(f"Telegram {operation} returned no result")
    return result.get("result")


def send_text(config: Mapping[str, Any], text: str) -> int:
    """Send text to the configured chat, in sequential 4096-character chunks.

    The returned value is the last Telegram ``message_id``.  If a later chunk
    fails, earlier chunks may already have been accepted by Telegram.
    """

    config = _require_config(config)
    token = _token(config)
    chat_id = _chat_id(config)
    if not isinstance(text, str):
        raise TypeError("Telegram text must be a string")
    if not text:
        raise ValueError("Telegram text must not be empty")

    last_message_id: Optional[int] = None
    for start in range(0, len(text), MAX_TEXT_LENGTH):
        chunk = text[start : start + MAX_TEXT_LENGTH]
        response = _request_json(
            token,
            "sendMessage",
            payload={"chat_id": chat_id, "text": chunk},
            operation="sendMessage request",
        )
        message = _result(response, "sendMessage request")
        if not isinstance(message, Mapping):
            raise TelegramError("Telegram sendMessage response did not include a message")
        message_id = message.get("message_id")
        if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
            raise TelegramError("Telegram sendMessage response did not include a message id")
        last_message_id = message_id

    # The empty-text guard above means this is unreachable, but retaining the
    # check keeps the return contract explicit if chunking changes later.
    if last_message_id is None:
        raise TelegramError("Telegram sendMessage returned no message id")
    return last_message_id


def get_bot(config: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the configured bot token with ``getMe`` and return its identity."""

    config = _require_config(config)
    token = _token(config)
    response = _request_json(token, "getMe", operation="getMe request")
    bot = _result(response, "getMe request")
    if not isinstance(bot, Mapping):
        raise TelegramError("Telegram getMe response did not include a bot")
    bot_id = bot.get("id")
    username = bot.get("username")
    if isinstance(bot_id, bool) or not isinstance(bot_id, int) or bot_id <= 0:
        raise TelegramError("Telegram getMe response did not include a bot id")
    if not isinstance(username, str) or not username:
        raise TelegramError("Telegram getMe response did not include a bot username")
    return {"id": bot_id, "username": username}


def _webhook_is_active(config: Mapping[str, Any], token: str) -> None:
    response = _request_json(token, "getWebhookInfo", operation="getWebhookInfo request")
    info = _result(response, "getWebhookInfo request")
    if not isinstance(info, Mapping):
        raise TelegramError("Telegram getWebhookInfo response did not include webhook data")
    url = info.get("url")
    if not isinstance(url, str):
        raise TelegramError("Telegram getWebhookInfo response did not include webhook status")
    if url:
        raise TelegramError(
            "Telegram webhook is active; use manual Chat ID binding for a bot with an existing "
            "webhook. Polling exposes only the oldest 100 pending updates, so a challenge "
            "outside that window also requires manual Chat ID binding"
        )


def _private_message_binding(message: Any, expected_text: str, issued_at: float) -> Optional[dict[str, Any]]:
    if not isinstance(message, Mapping):
        return None
    if message.get("text") != expected_text:
        return None
    date = message.get("date")
    if isinstance(date, bool) or not isinstance(date, (int, float)):
        return None
    if date < issued_at - 5:
        return None

    chat = message.get("chat")
    sender = message.get("from")
    if not isinstance(chat, Mapping) or not isinstance(sender, Mapping):
        return None
    if chat.get("type") != "private":
        return None
    chat_id = chat.get("id")
    sender_id = sender.get("id")
    if (
        isinstance(chat_id, bool)
        or not isinstance(chat_id, int)
        or isinstance(sender_id, bool)
        or not isinstance(sender_id, int)
        or sender_id != chat_id
    ):
        return None

    first_name = sender.get("first_name")
    last_name = sender.get("last_name")
    username = sender.get("username")
    if not isinstance(first_name, str) or not first_name:
        return None
    if last_name is not None and not isinstance(last_name, str):
        return None
    if username is not None and not isinstance(username, str):
        return None
    name = first_name if not last_name else f"{first_name} {last_name}"
    return {
        "id": sender_id,
        "name": name,
        "username": username,
        "chat_id": chat_id,
    }


def find_binding(config: Mapping[str, Any], nonce: str, issued_at: float) -> Optional[dict[str, Any]]:
    """Find a recent private ``/start <nonce>`` message without consuming updates.

    The request uses ``getUpdates?timeout=0`` with no offset, so unrelated bot
    updates remain available to the bot owner.  A configured webhook blocks
    this flow because Telegram does not expose pending updates to polling while
    a webhook is active; the webhook is never deleted by this function.
    """

    config = _require_config(config)
    token = _token(config)
    if not isinstance(nonce, str) or not nonce:
        raise ValueError("Telegram login nonce must be a non-empty string")
    if any(ch.isspace() for ch in nonce):
        raise ValueError("Telegram login nonce must not contain whitespace")
    if isinstance(issued_at, bool) or not isinstance(issued_at, (int, float)):
        raise ValueError("Telegram login issued time must be numeric")
    expected_text = "/start " + nonce

    _webhook_is_active(config, token)
    response = _request_json(
        token,
        "getUpdates",
        query={"timeout": 0},
        operation="getUpdates request",
        http_method="GET",
    )
    updates = _result(response, "getUpdates request")
    if not isinstance(updates, list):
        raise TelegramError("Telegram getUpdates response did not include updates")
    for update in updates:
        if not isinstance(update, Mapping):
            continue
        binding = _private_message_binding(update.get("message"), expected_text, float(issued_at))
        if binding is not None and hmac.compare_digest(
            update.get("message", {}).get("text", ""), expected_text
        ):
            return binding
    return None


__all__ = ["TelegramError", "find_binding", "get_bot", "send_text"]
