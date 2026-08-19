"""Pure helpers for Xianyu cookies, MTop signatures and IM frames."""

from __future__ import annotations

import base64
import hashlib
import json
import random
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal, cast

from miniagent.assistant.xianyu.errors import XianyuDependencyError, XianyuProtocolError

APP_KEY = "34839810"
IM_APP_KEY = "444e9908a51d1cb236a27862abc769c9"


def parse_cookie_header(value: str) -> dict[str, str]:
    """Parse a Cookie header without losing values containing ``=``."""
    result: dict[str, str] = {}
    for part in value.split(";"):
        name, separator, cookie_value = part.strip().partition("=")
        if separator and name:
            result[name] = cookie_value
    return result


def format_cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


def mtop_sign(timestamp_ms: str | int, token: str, data: str) -> str:
    raw = f"{token}&{timestamp_ms}&{APP_KEY}&{data}".encode()
    return hashlib.md5(raw).hexdigest()  # noqa: S324 - required by the MTop protocol


def generate_mid() -> str:
    return f"{random.SystemRandom().randrange(1000):03d}{int(time.time() * 1000)} 0"


def generate_message_uuid() -> str:
    return f"-{int(time.time() * 1000)}{uuid.uuid4().int % 10}"


def generate_device_id(user_id: str) -> str:
    return f"{str(uuid.uuid4()).upper()}-{user_id}"


def _msgpack() -> Any:
    try:
        import msgpack
    except ImportError as error:
        raise XianyuDependencyError(
            "Xianyu message decoding requires the 'msgpack' package; install miniagent-python[xianyu]"
        ) from error
    return msgpack


def decode_sync_payload(value: str) -> Any:
    """Decode the base64 MessagePack body carried by sync push frames."""
    try:
        raw = base64.b64decode(value, validate=True)
        return _msgpack().unpackb(raw, raw=False, strict_map_key=False)
    except XianyuDependencyError:
        raise
    except Exception as error:
        raise XianyuProtocolError("invalid Xianyu sync payload") from error


def decode_custom_data(value: str) -> dict[str, Any]:
    try:
        decoded = base64.b64decode(value).decode("utf-8")
        result = json.loads(decoded)
    except Exception as error:
        raise XianyuProtocolError("invalid Xianyu custom message data") from error
    if not isinstance(result, dict):
        raise XianyuProtocolError("Xianyu custom message data must be an object")
    return result


def encode_message_content(kind: Literal["text", "image"], value: dict[str, Any]) -> str:
    if kind == "text":
        payload = {"contentType": 1, "text": {"text": str(value["text"])}}
    elif kind == "image":
        payload = {
            "contentType": 2,
            "image": {
                "pics": [
                    {
                        "type": 0,
                        "url": str(value["url"]),
                        "width": int(value["width"]),
                        "height": int(value["height"]),
                    }
                ]
            },
        }
    else:  # pragma: no cover - protected by Literal callers
        raise ValueError(f"unsupported Xianyu message kind: {kind}")
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return base64.b64encode(raw).decode()


def build_ack(frame: dict[str, Any]) -> dict[str, Any]:
    headers = cast(dict[str, Any], frame.get("headers")) if isinstance(frame.get("headers"), dict) else {}
    ack_headers = {
        "mid": str(headers.get("mid") or generate_mid()),
        "sid": str(headers.get("sid") or ""),
    }
    for key in ("app-key", "ua", "dt"):
        if key in headers:
            ack_headers[key] = headers[key]
    return {"code": 200, "headers": ack_headers}


def build_registration(access_token: str, device_id: str) -> dict[str, Any]:
    return {
        "lwp": "/reg",
        "headers": {
            "cache-header": "app-key token ua wv",
            "app-key": IM_APP_KEY,
            "token": access_token,
            "ua": "Mozilla/5.0 DingTalk(2.1.5) OS(Windows/10) DingWeb/2.1.5",
            "dt": "j",
            "wv": "im:3,au:3,sy:6",
            "sync": "0,0;0;0;",
            "did": device_id,
            "mid": generate_mid(),
        },
    }


def build_sync_ack() -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    return {
        "lwp": "/r/SyncStatus/ackDiff",
        "headers": {"mid": generate_mid()},
        "body": [
            {
                "pipeline": "sync",
                "tooLong2Tag": "PNM,1",
                "channel": "sync",
                "topic": "sync",
                "highPts": 0,
                "pts": now_ms * 1000,
                "seq": 0,
                "timestamp": now_ms,
            }
        ],
    }


def build_send_message(
    *,
    owner_id: str,
    conversation_id: str,
    receiver_id: str,
    kind: Literal["text", "image"],
    value: dict[str, Any],
) -> dict[str, Any]:
    custom_type = 1 if kind == "text" else 2
    return {
        "lwp": "/r/MessageSend/sendByReceiverScope",
        "headers": {"mid": generate_mid()},
        "body": [
            {
                "uuid": generate_message_uuid(),
                "cid": f"{conversation_id}@goofish",
                "conversationType": 1,
                "content": {
                    "contentType": 101,
                    "custom": {
                        "type": custom_type,
                        "data": encode_message_content(kind, value),
                    },
                },
                "redPointPolicy": 0,
                "extension": {"extJson": "{}"},
                "ctx": {"appVersion": "1.0", "platform": "web"},
                "mtags": {},
                "msgReadStatusSetting": 1,
            },
            {"actualReceivers": [f"{receiver_id}@goofish", f"{owner_id}@goofish"]},
        ],
    }


def build_create_chat(owner_id: str, receiver_id: str, item_id: str) -> dict[str, Any]:
    return {
        "lwp": "/r/SingleChatConversation/create",
        "headers": {"mid": generate_mid()},
        "body": [
            {
                "pairFirst": f"{receiver_id}@goofish",
                "pairSecond": f"{owner_id}@goofish",
                "bizType": "1",
                "extension": {"itemId": item_id},
                "ctx": {"appVersion": "1.0", "platform": "web"},
            }
        ],
    }


def build_history_request(conversation_id: str, cursor: int) -> tuple[str, dict[str, Any]]:
    mid = generate_mid()
    return mid, {
        "lwp": "/r/MessageManager/listUserMessages",
        "headers": {"mid": mid},
        "body": [f"{conversation_id}@goofish", False, cursor, 20, False],
    }


@dataclass(frozen=True, slots=True)
class XianyuInbound:
    message_id: str
    conversation_id: str
    sender_id: str
    sender_name: str
    kind: Literal["text", "image"]
    text: str = ""
    image_url: str = ""
    occurred_at_ms: int = 0
    item_id: str = ""


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _candidate_message(decoded: Any) -> dict[str, Any] | None:
    candidates = [item for item in _walk(decoded) if isinstance(item, dict)]
    for item in candidates:
        extension = item.get("extension") or item.get("10") or item.get(10)
        if isinstance(extension, dict) and (
            extension.get("senderUserId") or extension.get("reminderContent")
        ):
            return item
    return None


def parse_inbound_frame(frame: dict[str, Any]) -> XianyuInbound | None:
    """Extract a supported buyer text/image message from one push frame."""
    try:
        raw = frame["body"]["syncPushPackage"]["data"][0]["data"]
    except (KeyError, IndexError, TypeError):
        return None
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            decoded = decode_sync_payload(raw)
    else:
        decoded = raw
    message = _candidate_message(decoded)
    if message is None:
        return None
    message_any: Any = message
    extension = message_any.get("extension") or message_any.get("10") or message_any.get(10) or {}
    content = message_any.get("content") or message_any.get("4") or message_any.get(4) or {}
    custom = content.get("custom") if isinstance(content, dict) else None
    payload: dict[str, Any] = {}
    if isinstance(custom, dict) and custom.get("data"):
        payload = decode_custom_data(str(custom["data"]))
    conversation = str(
        message_any.get("cid")
        or message_any.get("2")
        or message_any.get(2)
        or extension.get("sid")
        or ""
    ).split("@", 1)[0]
    sender_id = str(extension.get("senderUserId") or "")
    message_id = str(
        extension.get("messageId")
        or message_any.get("messageId")
        or message_any.get("uuid")
        or ""
    )
    timestamp = (
        message_any.get("createTime")
        or message_any.get("timestamp")
        or extension.get("timestamp")
        or 0
    )
    try:
        occurred_at_ms = int(timestamp)
    except (TypeError, ValueError):
        occurred_at_ms = 0
    content_type = payload.get("contentType")
    if content_type == 1:
        text = str(
            (payload.get("text") or {}).get("text") or extension.get("reminderContent") or ""
        )
        kind: Literal["text", "image"] = "text"
        image_url = ""
    elif content_type == 2:
        pics = (payload.get("image") or {}).get("pics") or []
        image_url = str(pics[0].get("url") or "") if pics and isinstance(pics[0], dict) else ""
        text = ""
        kind = "image"
    else:
        return None
    if not conversation or not sender_id or not message_id:
        return None
    return XianyuInbound(
        message_id=message_id,
        conversation_id=conversation,
        sender_id=sender_id,
        sender_name=str(extension.get("reminderTitle") or sender_id),
        kind=kind,
        text=text,
        image_url=image_url,
        occurred_at_ms=occurred_at_ms,
        item_id=str(extension.get("itemId") or ""),
    )


__all__ = [
    "APP_KEY",
    "IM_APP_KEY",
    "XianyuInbound",
    "build_ack",
    "build_create_chat",
    "build_history_request",
    "build_registration",
    "build_send_message",
    "build_sync_ack",
    "decode_custom_data",
    "decode_sync_payload",
    "format_cookie_header",
    "generate_device_id",
    "generate_message_uuid",
    "generate_mid",
    "mtop_sign",
    "parse_cookie_header",
    "parse_inbound_frame",
]
