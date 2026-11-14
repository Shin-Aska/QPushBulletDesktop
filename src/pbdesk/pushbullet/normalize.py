"""Normalize documented stored pushes and unencrypted mirror ephemerals."""
from datetime import datetime, timezone
import json
import math

# Stored pushes arrive through REST after a stream tickle, not as ephemerals.
DEMO = {"iden": "pbdesk-sanitized-demo-v1", "type": "note", "title": "pbdesk demo", "body": "A sanitized event received once by your private core.", "created": 1704067200}


def utc_timestamp(value):
    """Reject JSON booleans, nonfinite numbers, and nonrepresentable UTC times."""
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
        value = push.get(key, "")
        return value[:65536] if isinstance(value, str) else ""
    return {"source_id": source_id, "type": kind, "title": string("title"), "body": string("body"),
            "url": string("url") or None, "device_id": string("source_device_iden") or None,
            "created_at": created_at, "source_device_id": string("source_device_iden") or None,
            "target_device_id": string("target_device_iden") or None}
