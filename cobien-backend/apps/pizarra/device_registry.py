import os

from pymongo import MongoClient


_client = MongoClient(os.getenv("MONGO_URI"))
_db = _client[os.getenv("DB_NAME", "LabasAppDB")]

col_devices = _db["devices"]
col_user_device_access = _db["user_device_access"]


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
        {"$setOnInsert": {"device_id": device_id, "display_name": display_name, "contacts": contacts}},
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


def get_accessible_device_ids(username="", email=""):
    device_ids = []
    seen = set()

    if username:
        for doc in col_user_device_access.find({"username": username}).sort([("is_default", -1), ("device_id", 1)]):
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
        {"$set": {"device_id": device_id, "display_name": resolved_display, "contacts": contacts}},
        upsert=True,
    )


def list_device_assignments(device_id):
    return list(col_user_device_access.find({"device_id": device_id}).sort([("is_default", -1), ("username", 1)]))


def replace_device_assignments(device_id, usernames, default_username=""):
    device_id = str(device_id or "").strip()
    usernames = [str(value).strip() for value in usernames if str(value).strip()]
    default_username = str(default_username or "").strip()

    col_user_device_access.delete_many({"device_id": device_id})
    for username in usernames:
        col_user_device_access.insert_one(
            {
                "username": username,
                "device_id": device_id,
                "role": "member",
                "is_default": username == default_username,
            }
        )

    if default_username and default_username not in usernames:
        col_user_device_access.insert_one(
            {
                "username": default_username,
                "device_id": device_id,
                "role": "member",
                "is_default": True,
            }
        )
