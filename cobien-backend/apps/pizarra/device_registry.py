import json
import os
import secrets
from datetime import datetime, timezone, timedelta

from django.contrib.auth.hashers import check_password
from pymongo import MongoClient


_client = MongoClient(os.getenv("MONGO_URI"))
_db = _client[os.getenv("DB_NAME", "LabasAppDB")]

col_devices = _db["devices"]
col_user_device_access = _db["user_device_access"]


def _device_keys_from_env():
    raw = (os.getenv("COBIEN_DEVICE_VIDEOCALL_KEYS") or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    normalized = {}
    for key, value in payload.items():
        device_id = str(key or "").strip()
        device_key = str(value or "").strip()
        if device_id and device_key:
            normalized[device_id] = device_key
    return normalized


def get_device_videocall_key(device_id):
    device_id = str(device_id or "").strip()
    if not device_id:
        return ""
    return str(_device_keys_from_env().get(device_id) or "").strip()


def normalize_contacts_list(raw_contacts):
    contacts = []
    if not isinstance(raw_contacts, (list, tuple)):
        return contacts

    for item in raw_contacts:
        if isinstance(item, dict):
            display_name = (
                str(item.get("display_name") or item.get("name") or item.get("display") or "").strip()
            )
            user_name = (
                str(item.get("user_name") or item.get("username") or item.get("user") or "").strip()
            )
            image_url = str(item.get("image_url") or item.get("image") or "").strip()
            if display_name and user_name:
                contacts.append(
                    {
                        "display_name": display_name,
                        "user_name": user_name,
                        "image_url": image_url,
                    }
                )
            continue

        if item is not None:
            value = str(item).strip()
            if value:
                contacts.append(
                    {
                        "display_name": value,
                        "user_name": value,
                        "image_url": "",
                    }
                )

    return contacts


def normalize_username_list(raw_usernames):
    usernames = []
    seen = set()
    for item in raw_usernames or []:
        value = str(item or "").strip()
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        usernames.append(value)
    return usernames


def normalize_event_regions(raw_regions):
    if isinstance(raw_regions, str):
        raw_regions = raw_regions.splitlines()
    regions = []
    seen = set()
    for item in raw_regions or []:
        value = str(item or "").strip()
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        regions.append(value)
    return regions


def _legacy_profile_for_username(username="", email=""):
    for colname in ("auth_user", "users"):
        col = _db[colname]
        if username:
            doc = col.find_one({"username": username})
            if doc:
                return doc
        if email:
            doc = col.find_one({"email": email})
            if doc:
                return doc
    return None


def _legacy_profile_for_device(device_id):
    if not device_id:
        return None

    query = {
        "$or": [
            {"target_device": device_id},
            {"default_room": device_id},
            {"linked_device": device_id},
        ]
    }
    for colname in ("auth_user", "users"):
        doc = _db[colname].find_one(query)
        if doc:
            return doc
    return None


def get_or_create_device(device_id):
    device_id = str(device_id or "").strip()
    if not device_id:
        return None

    doc = col_devices.find_one({"device_id": device_id})
    if doc:
        return doc

    legacy = _legacy_profile_for_device(device_id)
    contacts = normalize_contacts_list((legacy or {}).get("contacts", []))
    display_name = device_id
    if legacy:
        display_name = (
            str(legacy.get("first_name") or legacy.get("username") or legacy.get("email") or device_id).strip()
            or device_id
        )

    col_devices.update_one(
        {"device_id": device_id},
        {
            "$setOnInsert": {
                "device_id": device_id,
                "display_name": display_name,
                "videocall_room": device_id,
                "enabled": True,
                "hidden_in_admin": False,
                "event_visibility_scope": "all",
                "event_regions": [],
                "deployment_profile": {},
                "contacts": contacts,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )
    return col_devices.find_one({"device_id": device_id})


def list_known_devices():
    device_ids = set()

    try:
        for doc in _db["pizarra_icso_snapshots"].find({}, {"device_id": 1}):
            value = str(doc.get("device_id") or "").strip()
            if value:
                device_ids.add(value)
    except Exception:
        pass

    for colname in ("auth_user", "users"):
        try:
            for doc in _db[colname].find({}, {"target_device": 1, "default_room": 1, "linked_device": 1}):
                for field in ("target_device", "default_room", "linked_device"):
                    value = str(doc.get(field) or "").strip()
                    if value:
                        device_ids.add(value)
        except Exception:
            pass

    try:
        for doc in col_devices.find({}, {"device_id": 1}):
            value = str(doc.get("device_id") or "").strip()
            if value:
                device_ids.add(value)
    except Exception:
        pass

    devices = []
    for device_id in sorted(device_ids, key=str.casefold):
        device = get_or_create_device(device_id)
        if device:
            devices.append(device)
    return devices


def device_online_status(device, online_window_minutes=5):
    last_seen = device.get("last_seen_at")
    if not isinstance(last_seen, datetime):
        return "unknown"
    now = datetime.now(timezone.utc)
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return "online" if now - last_seen <= timedelta(minutes=online_window_minutes) else "offline"


def _assignment_is_active(doc):
    """Return True if the assignment doc is within its valid date window (if any)."""
    now = datetime.now(timezone.utc)
    valid_from = doc.get("valid_from")
    valid_until = doc.get("valid_until")
    if valid_from and isinstance(valid_from, datetime):
        vf = valid_from if valid_from.tzinfo else valid_from.replace(tzinfo=timezone.utc)
        if now < vf:
            return False
    if valid_until and isinstance(valid_until, datetime):
        vu = valid_until if valid_until.tzinfo else valid_until.replace(tzinfo=timezone.utc)
        if now > vu:
            return False
    return True


def get_accessible_device_ids(username="", email=""):
    device_ids = []
    seen = set()

    if username:
        for doc in col_user_device_access.find({"username": username}).sort([("is_default", -1), ("device_id", 1)]):
            if not _assignment_is_active(doc):
                continue
            value = str(doc.get("device_id") or "").strip()
            if value and value not in seen:
                seen.add(value)
                device_ids.append(value)

    if device_ids:
        return device_ids

    legacy = _legacy_profile_for_username(username=username, email=email)
    if legacy:
        for field in ("target_device", "default_room", "linked_device"):
            value = str(legacy.get(field) or "").strip()
            if value and value not in seen:
                seen.add(value)
                device_ids.append(value)

    return device_ids


def get_default_device_id(username="", email=""):
    if username:
        doc = col_user_device_access.find_one({"username": username, "is_default": True})
        if doc and doc.get("device_id"):
            return str(doc["device_id"]).strip()

    device_ids = get_accessible_device_ids(username=username, email=email)
    return device_ids[0] if device_ids else ""


def get_device_contacts(device_id):
    device = get_or_create_device(device_id)
    if device and normalize_contacts_list(device.get("contacts", [])):
        return normalize_contacts_list(device.get("contacts", []))

    legacy = _legacy_profile_for_device(device_id)
    return normalize_contacts_list((legacy or {}).get("contacts", []))


def update_device_contacts(device_id, contacts, display_name=""):
    device = get_or_create_device(device_id) or {"device_id": device_id}
    resolved_display = str(display_name or device.get("display_name") or device_id).strip() or device_id
    col_devices.update_one(
        {"device_id": device_id},
        {"$set": {"device_id": device_id, "display_name": resolved_display, "contacts": contacts, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


def list_device_assignments(device_id):
    return list(col_user_device_access.find({"device_id": device_id}).sort([("is_default", -1), ("username", 1)]))


def replace_device_assignments(device_id, usernames, default_username="", date_ranges=None):
    """Replace all assignments for a device.

    date_ranges: optional dict mapping username -> {"valid_from": datetime|None, "valid_until": datetime|None}
    """
    device_id = str(device_id or "").strip()
    usernames = normalize_username_list(usernames)
    default_username = str(default_username or "").strip()
    date_ranges = date_ranges or {}

    col_user_device_access.delete_many({"device_id": device_id})
    for username in usernames:
        dr = date_ranges.get(username, {})
        doc = {
            "username": username,
            "device_id": device_id,
            "role": "member",
            "is_default": username == default_username,
        }
        if dr.get("valid_from"):
            doc["valid_from"] = dr["valid_from"]
        if dr.get("valid_until"):
            doc["valid_until"] = dr["valid_until"]
        col_user_device_access.insert_one(doc)

    if default_username and default_username not in usernames:
        col_user_device_access.insert_one(
            {
                "username": default_username,
                "device_id": device_id,
                "role": "member",
                "is_default": True,
            }
        )


def get_user_device_assignments(username):
    """Return all device assignments for a user (with date range info)."""
    return list(
        col_user_device_access.find({"username": username}).sort("device_id", 1)
    )


def set_user_device_assignments(username, assignments):
    """Replace all device assignments for a user.

    assignments: list of dicts with keys:
        device_id (str), is_default (bool),
        valid_from (datetime|None), valid_until (datetime|None)
    """
    username = str(username or "").strip()
    if not username:
        return
    col_user_device_access.delete_many({"username": username})
    for a in assignments:
        device_id = str(a.get("device_id") or "").strip()
        if not device_id:
            continue
        doc = {
            "username": username,
            "device_id": device_id,
            "role": "member",
            "is_default": bool(a.get("is_default", False)),
        }
        if a.get("valid_from"):
            doc["valid_from"] = a["valid_from"]
        if a.get("valid_until"):
            doc["valid_until"] = a["valid_until"]
        col_user_device_access.insert_one(doc)


def create_device(device_id, display_name="", enabled=True, hidden_in_admin=False, videocall_room="", event_visibility_scope="all", event_regions=None, deployment_profile=None):
    device_id = str(device_id or "").strip()
    if not device_id:
        raise ValueError("device_id requerido")
    existing = col_devices.find_one({"device_id": device_id})
    if existing:
        raise ValueError(f"El dispositivo {device_id} ya existe")
    now = datetime.now(timezone.utc)
    col_devices.insert_one(
        {
            "device_id": device_id,
            "display_name": str(display_name or device_id).strip() or device_id,
            "videocall_room": str(videocall_room or device_id).strip() or device_id,
            "enabled": bool(enabled),
            "hidden_in_admin": bool(hidden_in_admin),
            "event_visibility_scope": "region" if str(event_visibility_scope or "").strip().lower() == "region" else "all",
            "event_regions": normalize_event_regions(event_regions),
            "deployment_profile": deployment_profile if isinstance(deployment_profile, dict) else {},
            "contacts": [],
            "created_at": now,
            "updated_at": now,
            "last_seen_at": None,
        }
    )


def update_device_metadata(device_id, display_name="", enabled=True, hidden_in_admin=False, videocall_room="", event_visibility_scope="all", event_regions=None, deployment_profile=None):
    device_id = str(device_id or "").strip()
    if not device_id:
        raise ValueError("device_id requerido")
    col_devices.update_one(
        {"device_id": device_id},
        {
            "$set": {
                "display_name": str(display_name or device_id).strip() or device_id,
                "videocall_room": str(videocall_room or device_id).strip() or device_id,
                "enabled": bool(enabled),
                "hidden_in_admin": bool(hidden_in_admin),
                "event_visibility_scope": "region" if str(event_visibility_scope or "").strip().lower() == "region" else "all",
                "event_regions": normalize_event_regions(event_regions),
                "deployment_profile": deployment_profile if isinstance(deployment_profile, dict) else {},
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )


def touch_device_heartbeat(device_id, payload=None):
    device_id = str(device_id or "").strip()
    if not device_id:
        raise ValueError("device_id requerido")
    payload = payload or {}
    get_or_create_device(device_id)
    update_fields = {
        "last_seen_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "heartbeat": payload,
    }
    hardware_summary = payload.get("hardware_summary")
    hardware_inventory = payload.get("hardware_inventory")
    if isinstance(hardware_summary, dict) and hardware_summary:
        update_fields["hardware_summary"] = hardware_summary
        update_fields["hardware_reported_at"] = datetime.now(timezone.utc)
    if isinstance(hardware_inventory, dict) and hardware_inventory:
        update_fields["hardware_inventory"] = hardware_inventory
        update_fields["hardware_reported_at"] = datetime.now(timezone.utc)
    software_version = str(payload.get("software_version") or "").strip()
    if software_version:
        update_fields["software_version"] = software_version

    col_devices.update_one(
        {"device_id": device_id},
        {
            "$set": update_fields
        },
    )
    return col_devices.find_one({"device_id": device_id})


def get_device_videocall_context(device_id):
    device_id = str(device_id or "").strip()
    if not device_id:
        return None

    device = col_devices.find_one({"device_id": device_id})
    if not device:
        return None
    if device.get("enabled", True) is False:
        return None

    allowed_room = str(device.get("videocall_room") or device_id).strip() or device_id
    return {
        "device": device,
        "device_id": device_id,
        "allowed_room": allowed_room,
        "display_name": str(device.get("display_name") or device_id).strip() or device_id,
    }


def resolve_device_id_for_queue_target(target):
    value = str(target or "").strip()
    if not value:
        return ""

    direct = col_devices.find_one({"device_id": value})
    if direct:
        return str(direct.get("device_id") or "").strip()

    by_room = col_devices.find_one({"videocall_room": value})
    if by_room:
        return str(by_room.get("device_id") or "").strip()

    by_name = col_devices.find_one({"display_name": value})
    if by_name:
        return str(by_name.get("device_id") or "").strip()

    lowered = value.casefold()
    for doc in col_devices.find({}, {"device_id": 1, "videocall_room": 1, "display_name": 1}):
        for field in ("device_id", "videocall_room", "display_name"):
            candidate = str(doc.get(field) or "").strip()
            if candidate and candidate.casefold() == lowered:
                return str(doc.get("device_id") or "").strip()

    return value


def verify_device_videocall_key(device_id, provided_key):
    provided_key = str(provided_key or "").strip()
    if not provided_key:
        return False

    ctx = get_device_videocall_context(device_id)
    if not ctx:
        return False

    device = ctx["device"]
    stored_hash = str(device.get("videocall_device_key_hash") or "").strip()
    if stored_hash:
        try:
            return check_password(provided_key, stored_hash)
        except Exception:
            return False

    env_keys = _device_keys_from_env()
    env_value = env_keys.get(ctx["device_id"])
    if env_value:
        return secrets.compare_digest(provided_key, env_value)

    shared_key = str(os.getenv("COBIEN_DEVICE_VIDEOCALL_KEY") or "").strip()
    if shared_key:
        return secrets.compare_digest(provided_key, shared_key)

    return False
