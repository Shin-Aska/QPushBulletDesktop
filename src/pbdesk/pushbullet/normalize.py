"""Normalize documented stored pushes and unencrypted mirror ephemerals."""
from datetime import datetime, timezone
import json
import math

# Keep only bounded public media metadata, never complete provider objects.
MAX_INLINE_IMAGE = 32768
MAX_MEDIA_URL = 4096


def media_string(value, limit):
    """Return a non-empty string within the length limit, or None.

    :param value: Candidate media text or URL value to validate.
    :type value: object
    :param limit: Maximum permitted character length.
    :type limit: int
    :return: Validated non-empty string within ``limit`` characters, or ``None``.
    :rtype: str | None
    """
    return value if isinstance(value, str) and 0 < len(value) <= limit else None


def media_recipients(value):
    """Sanitize up to 16 SMS recipient entries within media size bounds.

    :param value: Raw recipient list or arbitrary field from a thread object.
    :type value: object
    :return: List of sanitized recipient dictionaries bounded in count and thumbnail size.
    :rtype: list[dict[str, str]]
    """
    result = []
    budget = MAX_INLINE_IMAGE
    for recipient in value[:16] if isinstance(value, list) else []:
        if not isinstance(recipient, dict):
            continue
        clean = {}
        for field, limit in (("address", 256), ("name", 256), ("image_url", MAX_MEDIA_URL)):
            text = media_string(recipient.get(field), limit)
            if text:
                clean[field] = text
        if "address" not in clean:
            address = media_string(recipient.get("number"), 256)
            if address:
                clean["address"] = address
        thumbnail = media_string(recipient.get("thumbnail"), budget)
        if thumbnail:
            clean["thumbnail"] = thumbnail
            budget -= len(thumbnail)
        if clean:
            result.append(clean)
    return result


def media_urls(value):
    """Extract up to 16 bounded media URL strings from a list value.

    :param value: Candidate list of media URL values.
    :type value: object
    :return: Validated non-empty URL strings bounded by ``MAX_MEDIA_URL`` and capped at 16 items.
    :rtype: list[str]
    """
    return [url for raw in value[:16] if (url := media_string(raw, MAX_MEDIA_URL))] if isinstance(value, list) else []


# Stored pushes arrive through REST after a stream tickle, not as ephemerals.
DEMO = {"iden": "pbdesk-sanitized-demo-v1", "type": "note", "title": "pbdesk demo", "body": "A sanitized event received once by your private core.", "created": 1704067200}


def utc_timestamp(value):
    """Reject JSON booleans, nonfinite numbers, and nonrepresentable UTC times.

    :param value: Candidate UNIX timestamp value in seconds to validate.
    :type value: object
    :return: Validated non-negative finite UTC timestamp in seconds, or ``None`` if invalid.
    :rtype: float | None
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        timestamp = float(value)
        if not math.isfinite(timestamp) or timestamp < 0:
            return None
        datetime.fromtimestamp(timestamp, timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None
    return timestamp


def normalize(payload):
    """Normalize a raw Pushbullet push payload into an internal item dictionary, or None.

    :param payload: Raw Pushbullet push dictionary or stream push wrapper.
    :type payload: object
    :return: Normalized internal item dictionary, or ``None`` if unsupported, encrypted, dismissed, or invalid.
    :rtype: dict[str, Any] | None
    """
    if not isinstance(payload, dict):
        return None
    push = payload.get("push") if payload.get("type") == "push" else payload
    if not isinstance(push, dict):
        return None
    kind = push.get("type")
    if not isinstance(kind, str) or push.get("encrypted"):
        return None
    if kind == "mirror":
        device, package = push.get("source_device_iden"), push.get("package_name")
        notification_id, tag = push.get("notification_id"), push.get("notification_tag", "")
        if not isinstance(device, str) or not device or not isinstance(package, str) or not package:
            return None
        if isinstance(notification_id, bool) or not isinstance(notification_id, (str, int)) or notification_id == "":
            return None
        if tag is not None and not isinstance(tag, str):
            return None
        identity = [device, package, notification_id, tag]
        source_id = "mirror:" + json.dumps(identity, separators=(",", ":"))
        created = datetime.now(timezone.utc).timestamp()
    elif kind in {"note", "link", "file", "address", "list"} and isinstance(push.get("iden"), str) and push["iden"]:
        if push.get("dismissed") or push.get("active") is False:
            return None
        source_id = "stored:" + push["iden"]
        created = utc_timestamp(push.get("created", 0))
        if created is None:
            return None
    else:
        return None
    created_at = datetime.fromtimestamp(created, timezone.utc).isoformat()
    def string(key):
        """Extract a string field from the push payload truncated to 65,536 characters.

        :param key: Field name to look up in the push dictionary.
        :type key: str
        :return: String field truncated to 65,536 characters, or ``""`` if absent or non-string.
        :rtype: str
        """
        value = push.get(key, "")
        return value[:65536] if isinstance(value, str) else ""
    return {"source_id": source_id, "type": kind, "title": string("title"), "body": string("body"),
            "url": string("url") or None, "device_id": string("source_device_iden") or None,
            "created_at": created_at, "source_device_id": string("source_device_iden") or None,
            "target_device_id": string("target_device_iden") or None,
            "application_name": media_string(push.get("application_name"), 256),
            "icon": media_string(push.get("icon"), MAX_INLINE_IMAGE),
            "file_name": media_string(push.get("file_name"), 1024),
            "file_type": media_string(push.get("file_type"), 256),
            "file_url": media_string(push.get("file_url"), MAX_MEDIA_URL)}
