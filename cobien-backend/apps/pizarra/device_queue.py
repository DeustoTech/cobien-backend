import copy
import os
from datetime import datetime, timezone, timedelta

from bson import ObjectId
from pymongo import ASCENDING, MongoClient

from .device_registry import list_known_devices


_client = MongoClient(os.getenv("MONGO_URI"))
_db = _client[os.getenv("DB_NAME", "LabasAppDB")]

col_device_queue = _db["pizarra_device_queue"]

try:
    col_device_queue.create_index([("device_id", ASCENDING), ("delivered", ASCENDING), ("created_at", ASCENDING)])
    col_device_queue.create_index("expire_at", expireAfterSeconds=0)
except Exception:
    pass


def _utc_now():
    return datetime.now(timezone.utc)


def _normalize_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("payload debe ser un objeto JSON")
    return copy.deepcopy(payload)


def enqueue_notification(device_id, payload, ttl_hours=24):
    device_id = str(device_id or "").strip()
    if not device_id:
        raise ValueError("device_id requerido")

    normalized_payload = _normalize_payload(payload)
    now = _utc_now()
    expire_at = now + timedelta(hours=int(ttl_hours)) if ttl_hours and int(ttl_hours) > 0 else None

    if not normalized_payload.get("to"):
        normalized_payload["to"] = device_id

    doc = {
        "device_id": device_id,
        "payload": normalized_payload,
        "created_at": now,
        "delivered": False,
        "delivered_at": None,
    }
    if expire_at:
        doc["expire_at"] = expire_at

    result = col_device_queue.insert_one(doc)
    return str(result.inserted_id)


def enqueue_broadcast_notification(payload, ttl_hours=24, device_ids=None):
    normalized_payload = _normalize_payload(payload)
    resolved_ids = []

    if device_ids is None:
        for device in list_known_devices():
            device_id = str(device.get("device_id") or "").strip()
            if device_id:
                resolved_ids.append(device_id)
    else:
        for device_id in device_ids:
            value = str(device_id or "").strip()
            if value:
                resolved_ids.append(value)

    inserted_ids = []
    seen = set()
    for device_id in resolved_ids:
        key = device_id.casefold()
        if key in seen:
            continue
        seen.add(key)
        item_payload = copy.deepcopy(normalized_payload)
        item_payload["to"] = device_id
        inserted_ids.append(enqueue_notification(device_id, item_payload, ttl_hours=ttl_hours))

    return inserted_ids


def claim_pending_notifications(device_id, limit=50):
    device_id = str(device_id or "").strip()
    if not device_id:
        raise ValueError("device_id requerido")

    try:
        limit = min(max(int(limit), 1), 200)
    except Exception:
        limit = 50

    docs = list(
        col_device_queue.find(
            {"device_id": device_id, "delivered": False},
        ).sort("created_at", ASCENDING).limit(limit)
    )

    if not docs:
        return []

    now = _utc_now()
    ids = [doc["_id"] for doc in docs if doc.get("_id")]
    col_device_queue.update_many(
        {"_id": {"$in": ids}, "delivered": False},
        {"$set": {"delivered": True, "delivered_at": now}},
    )

    notifications = []
    for doc in docs:
        payload = copy.deepcopy(doc.get("payload") or {})
        payload.setdefault("to", device_id)
        payload["queue_id"] = str(doc.get("_id") or ObjectId())
        notifications.append(payload)
    return notifications