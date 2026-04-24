import csv
import io
import os
import gridfs
import json
import re
import secrets
import string
import uuid
import base64
from bson import ObjectId
from datetime import datetime, timezone, timedelta
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.http import HttpResponseNotAllowed
from pymongo import MongoClient, DESCENDING, ASCENDING
from django.contrib.auth import get_user_model, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import render, redirect
from django.contrib import messages
from django.urls import reverse
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from .forms import DeviceAdminForm, DeviceContactsAdminForm, PizarraPostForm
from .device_registry import (
    col_devices,
    col_user_device_access,
    create_device,
    device_online_status,
    get_accessible_device_ids,
    get_device_videocall_key,
    get_default_device_id,
    get_device_contacts,
    get_or_create_device,
    list_device_assignments,
    list_known_devices,
    normalize_contacts_list,
    normalize_username_list,
    replace_device_assignments,
    touch_device_heartbeat,
    update_device_metadata,
    update_device_contacts,
)
from .device_queue import claim_pending_notifications, enqueue_broadcast_notification, enqueue_notification

# --- Mongo / GridFS ---
_client = MongoClient(os.getenv("MONGO_URI"))
_dbname = os.getenv("DB_NAME", "LabasAppDB")
db = _client[_dbname]
fs = gridfs.GridFS(db, collection="pizarra_fs")
fs_contacts = gridfs.GridFS(db, collection="pizarra_contacts_fs")
fs_people = gridfs.GridFS(db, collection="pizarra_people_fs")
col_messages = db["pizarra_messages"]

# --- Notificaciones ---
col_notifications = db["pizarra_notifications"]
col_icso_snapshots = db["pizarra_icso_snapshots"]
col_icso_events = db["pizarra_icso_events"]
col_device_runtime_logs = db["pizarra_device_runtime_logs"]
col_directory_people = db["pizarra_directory_people"]
try:
    # Búsqueda rápida por usuario/estado/fecha
    col_notifications.create_index([
        ("to_user", ASCENDING),
        ("read", ASCENDING),
        ("created_at", DESCENDING),
    ])
    col_notifications.create_index("expire_at", expireAfterSeconds=0)
except Exception:
    pass

try:
    col_icso_snapshots.create_index([("device_id", ASCENDING)], unique=True)
    col_icso_snapshots.create_index([("updated_at", DESCENDING)])
    col_icso_events.create_index([("device_id", ASCENDING), ("logged_at", DESCENDING)])
    col_icso_events.create_index([("created_at", DESCENDING)])
    col_device_runtime_logs.create_index(
        [("device_id", ASCENDING), ("log_type", ASCENDING), ("log_date", DESCENDING)],
        unique=True,
    )
    col_device_runtime_logs.create_index([("device_id", ASCENDING), ("updated_at", DESCENDING)])
except Exception:
    pass


_PALETTE = [
    "#A3E635", "#F472B6", "#F59E0B", "#34D399",
    "#F87171", "#C084FC", "#FB7185", "#FBBF24",
]

_DEVICE_RUNTIME_LOG_TYPES = {
    "app": {
        "label": "Application",
        "prefix": "cobien-app",
    },
    "can_bus": {
        "label": "CAN Bus",
        "prefix": "can-bus",
    },
    "mqtt_can_bridge": {
        "label": "MQTT-CAN Bridge",
        "prefix": "mqtt-can-bridge",
    },
}


def _color_for_device(name: str) -> str:
    if not name:
        return "#9CA3AF"
    h = 0
    for ch in name:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return _PALETTE[h % len(_PALETTE)]


def _require_api_key(request):
    expected = getattr(settings, "NOTIFY_API_KEY", "")
    if not expected:
        return True
    provided = request.headers.get("X-API-KEY") or request.GET.get("api_key") or request.POST.get("api_key")
    return provided == expected


def _get_basic_auth_credentials(request):
    header = str(request.headers.get("Authorization") or "").strip()
    if not header.startswith("Basic "):
        return "", ""
    token = header[6:].strip()
    if not token:
        return "", ""
    try:
        decoded = base64.b64decode(token).decode("utf-8")
    except Exception:
        return "", ""
    username, _, password = decoded.partition(":")
    return username.strip(), password


def _authenticate_staff_from_request(request):
    if getattr(request, "user", None) and request.user.is_authenticated and _staff_required(request.user):
        return request.user
    username, password = _get_basic_auth_credentials(request)
    if not username or not password:
        return None
    user = authenticate(request, username=username, password=password)
    if user and _staff_required(user):
        return user
    return None


def _admin_api_auth_failed_response():
    response = JsonResponse({"ok": False, "error": "Admin authentication required."}, status=401)
    response["WWW-Authenticate"] = 'Basic realm="CoBien Admin API"'
    return response


def _read_api_payload(request):
    try:
        payload = request.POST.dict()
        if payload:
            return payload
    except Exception:
        pass
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return {}


def _serialize_datetime(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _normalize_device_runtime_log_type(value):
    normalized = str(value or "").strip().lower().replace("-", "_")
    return normalized if normalized in _DEVICE_RUNTIME_LOG_TYPES else ""


def _normalize_device_runtime_log_date(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except Exception:
        return ""


def _build_device_runtime_logs_payload(device_id, days=2):
    now = datetime.now(timezone.utc)
    if not device_id:
        return {
            "runtime_logs": [],
            "runtime_logs_by_type": [],
            "runtime_logs_count": 0,
            "runtime_logs_available_types": [],
            "runtime_logs_available_dates": [],
            "runtime_logs_updated_at": "",
            "runtime_logs_status": "empty",
            "runtime_logs_status_label": "Waiting for sync",
        }

    cursor = (
        col_device_runtime_logs
        .find({"device_id": device_id}, {"_id": 0})
        .sort([("log_date", DESCENDING), ("updated_at", DESCENDING)])
    )

    docs = []
    seen_dates = set()
    available_dates = []
    available_types = set()
    updated_at = ""
    for doc in cursor:
        log_date = _normalize_device_runtime_log_date(doc.get("log_date"))
        log_type = _normalize_device_runtime_log_type(doc.get("log_type"))
        if not log_date or not log_type:
            continue
        if log_date not in seen_dates:
            seen_dates.add(log_date)
            available_dates.append(log_date)
        if len(seen_dates) > max(int(days or 2), 1):
            continue
        meta = _DEVICE_RUNTIME_LOG_TYPES.get(log_type, {})
        updated_value = _serialize_datetime(doc.get("updated_at"))
        docs.append(
            {
                "log_type": log_type,
                "log_type_label": meta.get("label") or log_type,
                "log_date": log_date,
                "filename": str(doc.get("filename") or "").strip(),
                "content": str(doc.get("content") or ""),
                "line_count": int(doc.get("line_count") or 0),
                "byte_count": int(doc.get("byte_count") or 0),
                "truncated": bool(doc.get("truncated")),
                "updated_at": updated_value,
                "empty": not bool(str(doc.get("content") or "").strip()),
            }
        )
        available_types.add(log_type)
        if updated_value and (not updated_at or str(updated_value) > str(updated_at)):
            updated_at = updated_value

    grouped = []
    for log_type, meta in _DEVICE_RUNTIME_LOG_TYPES.items():
        items = [item for item in docs if item["log_type"] == log_type]
        if not items:
            continue
        grouped.append(
            {
                "log_type": log_type,
                "log_type_label": meta["label"],
                "entries": items,
            }
        )

    return {
        "runtime_logs": docs,
        "runtime_logs_by_type": grouped,
        "runtime_logs_count": len(docs),
        "runtime_logs_available_types": list(available_types),
        "runtime_logs_available_dates": available_dates[: max(int(days or 2), 1)],
        "runtime_logs_updated_at": updated_at,
        "runtime_logs_status": (
            "fresh"
            if updated_at and (now - (_parse_datetime_value(updated_at, fallback=now) or now)).total_seconds() < 3600
            else "stale"
            if updated_at
            else "empty"
        ),
        "runtime_logs_status_label": (
            "Fresh"
            if updated_at and (now - (_parse_datetime_value(updated_at, fallback=now) or now)).total_seconds() < 3600
            else "Needs refresh"
            if updated_at
            else "Waiting for sync"
        ),
    }


def _device_hardware_sections(device):
    summary = device.get("hardware_summary") if isinstance(device, dict) else {}
    if not isinstance(summary, dict):
        summary = {}
    sections = []
    for key, label in (
        ("system", "Sistema"),
        ("cpu", "CPU"),
        ("graphics", "Gráfica"),
        ("audio", "Audio"),
        ("camera", "Cámara web"),
        ("display", "Pantalla"),
    ):
        item = summary.get(key)
        if not isinstance(item, dict):
            item = {}
        hardware = str(item.get("hardware") or "").strip()
        driver = str(item.get("driver") or "").strip()
        if not hardware and not driver:
            continue
        sections.append({"key": key, "label": label, "hardware": hardware, "driver": driver})
    return sections


def _device_icso_payload(device_id):
    snapshot = None
    events = []
    sources = []
    if not device_id:
        return {"snapshot": None, "events": [], "sources": [], "events_count": 0}

    snapshot_doc = col_icso_snapshots.find_one({"device_id": device_id})
    if snapshot_doc:
        payload = snapshot_doc.get("payload", {})
        snapshot = {
            "device_id": snapshot_doc.get("device_id"),
            "updated_at": _serialize_datetime(snapshot_doc.get("updated_at")),
            "captured_at": _serialize_datetime(snapshot_doc.get("captured_at")),
            "payload_items": _payload_items(payload),
            "payload_json": json.dumps(payload, indent=2, default=str, ensure_ascii=False),
            "payload_count": len(payload) if isinstance(payload, dict) else 0,
        }

    cursor = col_icso_events.find({"device_id": device_id}).sort("logged_at", DESCENDING).limit(100)
    for doc in cursor:
        item = _serialize_doc(doc)
        src = item.get("source", "") or "icso"
        events.append({
            "source": src,
            "logged_at": item.get("logged_at") or item.get("created_at") or "",
            "message": item.get("message", ""),
        })
        if src not in sources:
            sources.append(src)

    return {
        "snapshot": snapshot,
        "events": events,
        "sources": sources,
        "events_count": len(events),
    }


def _build_device_management_context(selected_device, show_hidden=False):
    devices = []
    for item in list_known_devices():
        if item.get("hidden_in_admin") and not show_hidden:
            continue
        devices.append(
            {
                "device_id": item.get("device_id"),
                "display_name": item.get("display_name") or item.get("device_id"),
                "enabled": item.get("enabled", True),
                "hidden_in_admin": item.get("hidden_in_admin", False),
                "videocall_room": str(item.get("videocall_room") or item.get("device_id") or "").strip(),
                "last_seen_at": _serialize_datetime(item.get("last_seen_at")),
                "status": device_online_status(item),
                "event_visibility_scope": str(item.get("event_visibility_scope") or "all").strip() or "all",
                "event_regions": list(item.get("event_regions") or []),
                "contacts_count": len(normalize_contacts_list(item.get("contacts", []))),
                "assigned_users_count": col_user_device_access.count_documents({"device_id": item.get("device_id")}),
                "hardware_sections": _device_hardware_sections(item),
                "hardware_reported_at": _serialize_datetime(item.get("hardware_reported_at")),
            }
        )

    device_ids = [item["device_id"] for item in devices]
    if not selected_device and device_ids:
        selected_device = device_ids[0]

    device_doc = get_or_create_device(selected_device) if selected_device else None
    contacts_text = _serialize_contacts_text((device_doc or {}).get("contacts", []))
    contact_rows = _contacts_for_template((device_doc or {}).get("contacts", []))
    profile_source = str((device_doc or {}).get("display_name") or "").strip()
    videocall_room = str((device_doc or {}).get("videocall_room") or selected_device or "").strip()
    enabled = bool((device_doc or {}).get("enabled", True))
    hidden_in_admin = bool((device_doc or {}).get("hidden_in_admin", False))
    event_visibility_scope = str((device_doc or {}).get("event_visibility_scope") or "all").strip() or "all"
    event_regions = list((device_doc or {}).get("event_regions") or [])
    assignments = list_device_assignments(selected_device) if selected_device else []
    assigned_users_text = _serialize_usernames_text(
        sorted(
            [str(item.get("username") or "").strip() for item in assignments if str(item.get("username") or "").strip()],
            key=str.casefold,
        )
    )
    default_username = ""
    for item in assignments:
        if item.get("is_default"):
            default_username = str(item.get("username") or "").strip()
            break
    deployment_profile_json = _default_deployment_profile_json(device_doc or {})

    icso_payload = _device_icso_payload(selected_device)
    runtime_logs_payload = _build_device_runtime_logs_payload(selected_device, days=2)
    people_profiles = _list_directory_people()

    all_users = list(
        db["auth_user"].find(
            {"is_active": True},
            {"username": 1, "first_name": 1, "last_name": 1, "email": 1, "_id": 0},
        ).sort("username", ASCENDING)
    )
    assigned_set = set(
        str(item.get("username") or "").strip()
        for item in assignments
        if str(item.get("username") or "").strip()
    )

    return {
        "devices": devices,
        "device_ids": device_ids,
        "selected_device": selected_device,
        "show_hidden": show_hidden,
        "contacts_text": contacts_text,
        "contact_rows": contact_rows,
        "profile_source": profile_source,
        "videocall_room": videocall_room,
        "enabled": enabled,
        "hidden_in_admin": hidden_in_admin,
        "event_visibility_scope": event_visibility_scope,
        "event_regions": event_regions,
        "event_regions_text": "\n".join(event_regions),
        "assigned_users_text": assigned_users_text,
        "default_username": default_username,
        "deployment_profile_json": deployment_profile_json,
        "device_env_download_url": reverse("pizarra_api_admin_device_env", kwargs={"device_id": selected_device}) if selected_device else "",
        "assignments_count": len(assignments),
        "contacts_count": len(normalize_contacts_list((device_doc or {}).get("contacts", []))),
        "last_seen_at": _serialize_datetime((device_doc or {}).get("last_seen_at")),
        "status": device_online_status(device_doc or {}),
        "hardware_sections": _device_hardware_sections(device_doc or {}),
        "hardware_reported_at": _serialize_datetime((device_doc or {}).get("hardware_reported_at")),
        "hardware_inventory_json": json.dumps((device_doc or {}).get("hardware_inventory", {}), indent=2, default=str, ensure_ascii=False),
        "people_profiles": people_profiles,
        "all_users": all_users,
        "assigned_set": assigned_set,
        **icso_payload,
        **runtime_logs_payload,
    }


def _parse_datetime_value(value, fallback=None):
    if isinstance(value, datetime):
        return value
    if not value:
        return fallback
    try:
        normalized = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except Exception:
        return fallback


def _parse_deployment_profile_json(raw_value):
    raw_value = str(raw_value or "").strip()
    if not raw_value:
        return {}
    try:
        payload = json.loads(raw_value)
    except Exception as exc:
        raise ValueError(f"Deployment profile JSON is invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Deployment profile JSON must be an object.")
    normalized = {}
    for key, value in payload.items():
        env_key = str(key or "").strip()
        if not env_key:
            continue
        normalized[env_key] = value
    return normalized


def _json_env_value(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _string_env_value(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _shell_quote_env_scalar(value):
    text = _string_env_value(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _resolve_portal_base_url(request=None, deployment_profile=None):
    profile = deployment_profile if isinstance(deployment_profile, dict) else {}
    override = str(profile.get("COBIEN_BACKEND_BASE_URL") or os.getenv("COBIEN_BACKEND_BASE_URL") or "").strip()
    if override:
        return override.rstrip("/")
    if request is not None:
        return f"{request.scheme}://{request.get_host()}".rstrip("/")
    return "https://portal.co-bien.eu"


def _default_deployment_profile_json(device_doc):
    payload = device_doc.get("deployment_profile") if isinstance(device_doc, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    if not payload:
        return "{\n  \n}"
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)


def _build_cobien_env_map(device_doc, request=None):
    device_doc = device_doc or {}
    device_id = str(device_doc.get("device_id") or "").strip()
    deployment_profile = device_doc.get("deployment_profile") if isinstance(device_doc.get("deployment_profile"), dict) else {}
    base_url = _resolve_portal_base_url(request=request, deployment_profile=deployment_profile)

    env_map = {
        "COBIEN_WORKSPACE_ROOT": "$HOME/cobien",
        "COBIEN_FRONTEND_REPO_NAME": "cobien_FrontEnd",
        "COBIEN_MQTT_REPO_NAME": "cobien_MQTT_Dictionnary",
        "COBIEN_UPDATE_REMOTE": "origin",
        "COBIEN_UPDATE_BRANCH": "master",
        "COBIEN_UPDATE_INTERVAL_SEC": "60",
        "COBIEN_LOG_RETENTION_DAYS": "90",
        "COBIEN_LOG_DIR": "$HOME/.local/state/cobien/logs",
        "COBIEN_RUNTIME_STATE_DIR": "$HOME/.local/state/cobien/runtime",
        "COBIEN_CACHE_DIR": "$HOME/.cache/cobien",
        "COBIEN_MODELS_DIR": "$HOME/.local/share/cobien/models/piper",
        "COBIEN_PIPER_RUNTIME_DIR": "$HOME/.local/share/cobien/piper/runtime",
        "COBIEN_INSTALL_SYSTEMD_USER": "1",
        "COBIEN_ENABLE_WATCH": "0",
        "COBIEN_INSTALL_SYSTEM_DEPS": "1",
        "COBIEN_RECREATE_VENV": "0",
        "COBIEN_NON_INTERACTIVE": "1",
        "COBIEN_AUTO_CONFIRM": "1",
        "COBIEN_INSTALL_RUSTDESK": "1",
        "COBIEN_RUSTDESK_VERSION": "1.4.6",
        "COBIEN_RUSTDESK_URL": "https://github.com/rustdesk/rustdesk/releases/download/1.4.6/rustdesk-1.4.6-x86_64.deb",
        "COBIEN_RUSTDESK_ARGS": "--tray",
        "COBIEN_AUTO_REBOOT_AFTER_SETUP": "1",
        "COBIEN_DISPLAY_OUTPUT": "eDP-1",
        "COBIEN_DISPLAY_MODE": "1920x1200",
        "COBIEN_DISPLAY_ROTATION": "inverted",
        "COBIEN_APP_LANGUAGE": "es",
        "COBIEN_DEVICE_ID": device_id,
        "COBIEN_VIDEOCALL_ROOM": str(device_doc.get("videocall_room") or device_id).strip() or device_id,
        "COBIEN_DEVICE_LOCATION": "Bilbao",
        "COBIEN_HARDWARE_MODE": "auto",
        "COBIEN_SETTINGS_PIN": "",
        "COBIEN_RESTART_PIN": "9999",
        "COBIEN_WEATHER_CITIES_JSON": ["Bilbao", "Toulouse", "Logroño"],
        "COBIEN_WEATHER_CITY_CATALOG_JSON": ["Bilbao", "Toulouse", "Logroño"],
        "COBIEN_WEATHER_PRIMARY_CITY": "Bilbao",
        "COBIEN_BUTTON_COLORS_JSON": {},
        "COBIEN_RFID_ACTIONS_JSON": {},
        "COBIEN_MICROPHONE_DEVICE": "",
        "COBIEN_AUDIO_OUTPUT_DEVICE": "",
        "COBIEN_JOKE_CATEGORY": "general",
        "COBIEN_IDLE_TIMEOUT_SEC": "60",
        "COBIEN_NOTIFICATIONS_JSON": {
            "videollamada": {"group": 1, "intensity": 255, "color": "#00FF00", "mode": "ON", "ringtone": ""},
            "nuevo_evento": {"group": 2, "intensity": 255, "color": "#FF0000", "mode": "ON", "ringtone": ""},
            "nueva_foto": {"group": 3, "intensity": 255, "color": "#0000FF", "mode": "BLINK", "ringtone": ""},
        },
        "COBIEN_BACKEND_BASE_URL": base_url,
        "COBIEN_NOTIFY_API_KEY": getattr(settings, "NOTIFY_API_KEY", ""),
        "COBIEN_VIDEOCALL_DEVICE_API_KEY": get_device_videocall_key(device_id),
        "COBIEN_DEVICE_POLL_URL": f"{base_url}/pizarra/api/device/poll/",
        "COBIEN_DEVICE_POLL_INTERVAL_SEC": "5",
        "COBIEN_DEVICE_HEARTBEAT_URL": f"{base_url}/pizarra/api/devices/heartbeat/",
        "COBIEN_DEVICE_HEARTBEAT_INTERVAL_SEC": "60",
        "COBIEN_PIZARRA_NOTIFY_URL": f"{base_url}/pizarra/api/notify/",
        "COBIEN_PIZARRA_API_URL": f"{base_url}/pizarra/api/messages/",
        "COBIEN_PIZARRA_DELETE_URL_TEMPLATE": f"{base_url}/pizarra/api/messages/{{post_id}}/delete/",
        "COBIEN_CONTACTS_API_URL": f"{base_url}/pizarra/api/contacts/",
        "COBIEN_ICSO_TELEMETRY_URL": f"{base_url}/pizarra/api/icso/telemetry/",
        "COBIEN_ICSO_EVENTS_URL": f"{base_url}/pizarra/api/icso/events/",
        "COBIEN_DEVICE_VIDEOCALL_SESSION_URL": f"{base_url}/api/device-videocall-session/",
        "COBIEN_PORTAL_VIDEOCALL_URL": f"{base_url}/videocall/",
        "COBIEN_PORTAL_VIDEOCALL_DEVICE_URL": f"{base_url}/videocall/device/",
        "COBIEN_PORTAL_CALL_ANSWERED_URL": f"{base_url}/api/call-answered/",
        "COBIEN_MQTT_LOCAL_BROKER": "localhost",
        "COBIEN_MQTT_LOCAL_PORT": "1883",
        "COBIEN_HTTP_TIMEOUT": "8",
        "OWM_API_KEY": os.getenv("OWM_API_KEY", ""),
        "NEWS_API_KEY": os.getenv("NEWS_API_KEY", ""),
        "MONGO_URI": os.getenv("MONGO_URI", ""),
        "COBIEN_TTS_ENGINE": "piper",
        "COBIEN_TTS_RATE": "155",
        "COBIEN_TTS_VOLUME": "0.85",
        "COBIEN_TTS_PIPER_BIN": "",
        "COBIEN_TTS_PIPER_MODEL_ES": "",
        "COBIEN_TTS_PIPER_MODEL_FR": "",
        "COBIEN_TTS_PIPER_MODEL_ES_MALE": "es_ES-davefx-medium",
        "COBIEN_TTS_PIPER_MODEL_ES_FEMALE": "es_ES-mls_10246-low",
        "COBIEN_TTS_PIPER_MODEL_FR_MALE": "fr_FR-mls_1840-low",
        "COBIEN_TTS_PIPER_MODEL_FR_FEMALE": "fr_FR-siwis-medium",
        "COBIEN_TTS_PIPER_MODEL_ES_URL": "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx",
        "COBIEN_TTS_PIPER_MODEL_FR_URL": "https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/mls_1840/low/fr_FR-mls_1840-low.onnx",
        "COBIEN_TTS_PIPER_MODEL_ES_MALE_URL": "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx",
        "COBIEN_TTS_PIPER_MODEL_ES_FEMALE_URL": "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/mls_10246/low/es_ES-mls_10246-low.onnx",
        "COBIEN_TTS_PIPER_MODEL_FR_MALE_URL": "https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/mls_1840/low/fr_FR-mls_1840-low.onnx",
        "COBIEN_TTS_PIPER_MODEL_FR_FEMALE_URL": "https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx",
        "COBIEN_TTS_PIPER_VOICE_ES": "male",
        "COBIEN_TTS_PIPER_VOICE_FR": "male",
        "COBIEN_DISABLE_SYSTEM_SLEEP": "1",
        "COBIEN_OPENWEATHER_CURRENT_URL": "https://api.openweathermap.org/data/2.5/weather",
        "COBIEN_OPENWEATHER_FORECAST_URL": "https://api.openweathermap.org/data/2.5/forecast",
        "COBIEN_NEWS_API_URL": "https://newsapi.org/v2/top-headlines",
        "COBIEN_OPEN_METEO_URL": "https://api.open-meteo.com/v1/forecast",
        "COBIEN_NOMINATIM_SEARCH_URL": "https://nominatim.openstreetmap.org/search",
        "COBIEN_BOOTSTRAP_PYTHON_VERSION": "3.13",
    }

    for key, value in deployment_profile.items():
        env_key = str(key or "").strip()
        if not env_key:
            continue
        env_map[env_key] = value

    return env_map


def _serialize_cobien_env(env_map):
    lines = []
    for key, value in env_map.items():
        if isinstance(value, (dict, list)):
            serialized = _json_env_value(value)
        else:
            serialized = _shell_quote_env_scalar(value)
        lines.append(f"{key}={serialized}")
    return "\n".join(lines) + "\n"


def _serialize_doc(doc):
    if not isinstance(doc, dict):
        return doc
    serialized = {}
    for key, value in doc.items():
        if key == "_id":
            serialized[key] = str(value)
        elif isinstance(value, dict):
            serialized[key] = _serialize_doc(value)
        elif isinstance(value, list):
            serialized[key] = [
                _serialize_doc(item) if isinstance(item, dict) else _serialize_datetime(item)
                for item in value
            ]
        else:
            serialized[key] = _serialize_datetime(value)
    return serialized

def _fetch_user_profile(request):
    username = getattr(request.user, "username", None)
    email = getattr(request.user, "email", None)
    for colname in ("auth_user", "users"):
        col = db[colname]
        if username:
            doc = col.find_one({"username": username})
            if doc:
                return doc
        if email:
            doc = col.find_one({"email": email})
            if doc:
                return doc
    return None


def _find_user_profile(username="", email=""):
    username = str(username or "").strip()
    email = str(email or "").strip()
    for colname in ("auth_user", "users"):
        col = db[colname]
        if username:
            doc = col.find_one({"username": username})
            if doc:
                return doc
        if email:
            doc = col.find_one({"email": email})
            if doc:
                return doc
    return None


def _profile_display_name(doc, fallback=""):
    if not isinstance(doc, dict):
        return str(fallback or "").strip()
    first_name = str(doc.get("first_name") or "").strip()
    last_name = str(doc.get("last_name") or "").strip()
    full_name = " ".join(part for part in (first_name, last_name) if part).strip()
    return full_name or str(doc.get("username") or fallback or "").strip()


def _user_avatar_url(username, request=None):
    username = str(username or "").strip()
    if not username:
        return ""
    avatar_fn = _user_avatar_filename(username)
    exists = db["pizarra_people_fs.files"].find_one({"filename": avatar_fn}, {"_id": 1})
    if not exists:
        return ""
    url = _directory_image_url(avatar_fn)
    if request is not None and str(url).startswith("/"):
        try:
            return request.build_absolute_uri(url)
        except Exception:
            return url
    return url


def _build_message_author_meta(username, request=None):
    username = str(username or "").strip()
    profile = _find_user_profile(username=username)
    author_name = _profile_display_name(profile, fallback=username) or username or "—"
    return {
        "author": username,
        "author_name": author_name,
        "author_avatar_url": _user_avatar_url(username, request=request),
        "author_initial": (author_name[:1] or username[:1] or "U").upper(),
    }


def _enqueue_board_reload(recipient_key, show_last=False):
    target = str(recipient_key or "").strip()
    if not target:
        return
    enqueue_notification(
        target,
        {
            "type": "board_reload",
            "target": "board",
            "reload_last": bool(show_last),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


def _mark_message_deleted_from_device(doc):
    if not isinstance(doc, dict) or not doc.get("_id"):
        return False
    image_file_id = doc.get("image_file_id")
    if image_file_id:
        try:
            fs.delete(ObjectId(str(image_file_id)))
        except Exception:
            pass
    result = col_messages.update_one(
        {"_id": doc["_id"]},
        {
            "$set": {
                "content": "Borrado desde el mueble. No se sincronizará.",
                "image_file_id": None,
                "deleted_from_device": True,
                "deleted_at": datetime.now(timezone.utc),
            }
        },
    )
    return bool(result.modified_count)


def _staff_required(user):
    return bool(getattr(user, "is_authenticated", False) and (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)))


def _find_profile_by_device(device_id: str):
    device_id = (device_id or "").strip()
    if not device_id:
        return None
    for colname in ("auth_user", "users"):
        doc = db[colname].find_one(
            {
                "$or": [
                    {"target_device": device_id},
                    {"default_room": device_id},
                    {"linked_device": device_id},
                ]
            }
        )
        if doc:
            return doc
    return None


def _list_known_device_ids():
    return [str(device.get("device_id") or "").strip() for device in list_known_devices() if str(device.get("device_id") or "").strip()]


def _parse_contacts_text(raw_text):
    contacts = []
    for line in str(raw_text or "").splitlines():
        value = line.strip()
        if not value:
            continue
        if "=" in value:
            display_name, user_name = value.split("=", 1)
            display_name = display_name.strip()
            user_name = user_name.strip()
        else:
            display_name = value
            user_name = value
        if not display_name or not user_name:
            continue
        contacts.append(
            {
                "display_name": display_name,
                "user_name": user_name,
                "image_url": "",
            }
        )
    return contacts


def _serialize_contacts_text(raw_contacts):
    lines = []
    for item in normalize_contacts_list(raw_contacts):
        lines.append(f"{item['display_name']}={item['user_name']}")
    return "\n".join(lines)


def _contact_media_dir():
    target = os.path.join(settings.MEDIA_ROOT, "pizarra_contacts")
    os.makedirs(target, exist_ok=True)
    return target


def _directory_media_dir():
    target = os.path.join(settings.MEDIA_ROOT, "pizarra_directory_people")
    os.makedirs(target, exist_ok=True)
    return target


def _contact_media_url(filename):
    return reverse("pizarra_contact_image", kwargs={"filename": filename})


def _directory_image_url(filename):
    return reverse("pizarra_directory_person_image", kwargs={"filename": filename})


def _contact_image_path_from_url(image_url):
    if not image_url:
        return ""
    filename = os.path.basename(str(image_url).split("?", 1)[0].rstrip("/"))
    if not filename:
        return ""
    return os.path.join(_contact_media_dir(), filename)


def _normalize_contact_image_url(image_url):
    value = str(image_url or "").strip()
    if not value:
        return ""
    if value.startswith("/pizarra/person-images/") or "/pizarra/person-images/" in value:
        return value
    if value.startswith("http://") or value.startswith("https://"):
        return value
    path = _contact_image_path_from_url(image_url)
    if not path or not os.path.exists(path):
        return ""
    return _contact_media_url(os.path.basename(path))


def _directory_image_path_from_url(image_url):
    if not image_url:
        return ""
    filename = os.path.basename(str(image_url).split("?", 1)[0].rstrip("/"))
    if not filename:
        return ""
    return os.path.join(_directory_media_dir(), filename)


def _contact_storage_name(device_id, display_name, filename):
    safe_device = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(device_id or "").strip()) or "device"
    safe_contact = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(display_name or "").strip()) or "contact"
    ext = os.path.splitext(str(filename or ""))[1].lower() or ".jpg"
    return f"{safe_device}-{safe_contact}-{uuid.uuid4().hex[:10]}{ext}"


def _gridfs_delete_by_filename(bucket, col_name, filename):
    """Delete all GridFS files matching filename in the given bucket."""
    if not filename:
        return
    for doc in db[f"{col_name}.files"].find({"filename": filename}, {"_id": 1}):
        try:
            bucket.delete(doc["_id"])
        except Exception:
            pass


def _delete_managed_contact_image(image_url):
    filename = os.path.basename(str(image_url).split("?", 1)[0].rstrip("/"))
    _gridfs_delete_by_filename(fs_contacts, "pizarra_contacts_fs", filename)
    # Backward-compat: also remove from local filesystem if it still exists
    path = _contact_image_path_from_url(image_url)
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _save_contact_image(device_id, display_name, uploaded_file):
    if not uploaded_file:
        return ""
    target_name = _contact_storage_name(device_id, display_name, uploaded_file.name)
    content_type = getattr(uploaded_file, "content_type", None) or "image/jpeg"
    uploaded_file.seek(0)
    fs_contacts.put(uploaded_file, filename=target_name, contentType=content_type)
    return _contact_media_url(target_name)


def _user_avatar_filename(username):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(username or "").strip()) or "user"
    return f"user-avatar-{safe}"


def _save_directory_image(display_name, uploaded_file):
    if not uploaded_file:
        return ""
    target_name = _contact_storage_name("directory", display_name, uploaded_file.name)
    content_type = getattr(uploaded_file, "content_type", None) or "image/jpeg"
    uploaded_file.seek(0)
    fs_people.put(uploaded_file, filename=target_name, contentType=content_type)
    return _directory_image_url(target_name)


def _save_user_avatar(username, uploaded_file):
    if not uploaded_file:
        return ""
    avatar_fn = _user_avatar_filename(username)
    _gridfs_delete_by_filename(fs_people, "pizarra_people_fs", avatar_fn)
    content_type = getattr(uploaded_file, "content_type", None) or "image/jpeg"
    uploaded_file.seek(0)
    fs_people.put(uploaded_file, filename=avatar_fn, contentType=content_type)
    return _directory_image_url(avatar_fn)


def _delete_directory_image(image_url):
    filename = os.path.basename(str(image_url).split("?", 1)[0].rstrip("/"))
    _gridfs_delete_by_filename(fs_people, "pizarra_people_fs", filename)
    path = _directory_image_path_from_url(image_url)
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _list_directory_people():
    """Return all active users as the central people directory."""
    existing_avatars = {
        doc["filename"]
        for doc in db["pizarra_people_fs.files"].find(
            {"filename": {"$regex": r"^user-avatar-"}}, {"filename": 1}
        )
    }
    items = []
    for doc in db["auth_user"].find({}, {"username": 1, "first_name": 1, "last_name": 1}).sort("username", ASCENDING):
        username = str(doc.get("username") or "").strip()
        if not username:
            continue
        display_name = " ".join(
            filter(None, [str(doc.get("first_name") or "").strip(), str(doc.get("last_name") or "").strip()])
        ) or username
        avatar_fn = _user_avatar_filename(username)
        image_url = _directory_image_url(avatar_fn) if avatar_fn in existing_avatars else ""
        items.append({
            "person_id": username,
            "display_name": display_name,
            "user_name": username,
            "image_url": image_url,
        })
    return items


def _send_provisional_password_email(request, user, password):
    subject = "Tu contraseña provisional en CoBien"
    ctx = {
        "user": user,
        "password": password,
        "site_url": request.build_absolute_uri('/'),
    }
    html_body = render_to_string("registration/provisional_password_email.html", ctx)
    txt_body  = render_to_string("registration/provisional_password_email.txt", ctx)
    msg = EmailMultiAlternatives(subject, txt_body, settings.DEFAULT_FROM_EMAIL, [user.email])
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=True)


def _list_users_for_admin():
    """Return all users with admin/active flags and avatar URLs."""
    existing_avatars = {
        doc["filename"]
        for doc in db["pizarra_people_fs.files"].find(
            {"filename": {"$regex": r"^user-avatar-"}}, {"filename": 1}
        )
    }
    items = []
    for doc in db["auth_user"].find({}, {
        "username": 1, "first_name": 1, "last_name": 1,
        "email": 1, "is_staff": 1, "is_active": 1, "_id": 0
    }).sort("username", ASCENDING):
        username = str(doc.get("username") or "").strip()
        if not username:
            continue
        avatar_fn = _user_avatar_filename(username)
        image_url = _directory_image_url(avatar_fn) if avatar_fn in existing_avatars else ""
        items.append({
            "username": username,
            "first_name": str(doc.get("first_name") or ""),
            "last_name": str(doc.get("last_name") or ""),
            "email": str(doc.get("email") or ""),
            "is_staff": bool(doc.get("is_staff", False)),
            "is_active": bool(doc.get("is_active", True)),
            "image_url": image_url,
        })
    return items


def _parse_contact_rows(request, device_id, existing_contacts):
    indices = set()
    for key in request.POST.keys():
        match = re.match(r"^contact_display_name_(\d+)$", str(key))
        if match:
            indices.add(int(match.group(1)))
    for key in request.FILES.keys():
        match = re.match(r"^contact_image_(\d+)$", str(key))
        if match:
            indices.add(int(match.group(1)))

    contacts = []
    previous_by_key = {}
    for item in normalize_contacts_list(existing_contacts):
        previous_by_key[(item["display_name"], item["user_name"])] = item

    seen = set()
    for idx in sorted(indices):
        display_name = str(request.POST.get(f"contact_display_name_{idx}", "") or "").strip()
        user_name = str(request.POST.get(f"contact_user_name_{idx}", "") or "").strip()
        previous_image_url = str(request.POST.get(f"contact_existing_image_url_{idx}", "") or "").strip()
        remove_image = request.POST.get(f"contact_remove_image_{idx}") == "1"
        uploaded_file = request.FILES.get(f"contact_image_{idx}")

        if not display_name and not user_name and not previous_image_url and not uploaded_file:
            continue
        if not display_name or not user_name:
            raise ValueError(f"Cada contacto debe tener nombre visible y username (fila {idx + 1}).")

        key = (display_name.casefold(), user_name.casefold())
        if key in seen:
            raise ValueError(f"Contacto duplicado: {display_name}={user_name}")
        seen.add(key)

        image_url = previous_image_url
        if remove_image:
            _delete_managed_contact_image(previous_image_url)
            image_url = ""
        if uploaded_file:
            _delete_managed_contact_image(previous_image_url)
            image_url = _save_contact_image(device_id, display_name, uploaded_file)

        contacts.append(
            {
                "display_name": display_name,
                "user_name": user_name,
                "image_url": image_url,
            }
        )

    current_keys = {(item["display_name"], item["user_name"]) for item in contacts}
    for item in normalize_contacts_list(existing_contacts):
        original_key = (item["display_name"], item["user_name"])
        if original_key not in current_keys:
            _delete_managed_contact_image(item.get("image_url", ""))

    return contacts


def _contacts_for_template(raw_contacts):
    contacts = []
    for item in normalize_contacts_list(raw_contacts):
        image_url = _normalize_contact_image_url(item.get("image_url", ""))
        contacts.append(
            {
                "display_name": item["display_name"],
                "user_name": item["user_name"],
                "image_url": image_url,
            }
        )
    return contacts


def _contacts_for_api(raw_contacts, request=None):
    contacts = []
    for item in normalize_contacts_list(raw_contacts):
        image_url = _normalize_contact_image_url(item.get("image_url", ""))
        if request is not None and image_url and str(image_url).startswith("/"):
            try:
                image_url = request.build_absolute_uri(image_url)
            except Exception:
                pass
        contacts.append(
            {
                "display_name": item["display_name"],
                "user_name": item["user_name"],
                "image_url": image_url,
            }
        )
    return contacts


def contact_image(request, filename):
    if not (getattr(request.user, "is_authenticated", False) or _require_api_key(request)):
        return JsonResponse({"error": "Unauthorized"}, status=401)
    safe_name = os.path.basename(str(filename or ""))
    # Serve from GridFS (persistent); fall back to local filesystem for old images
    try:
        grid_out = fs_contacts.get_last_version(filename=safe_name)
        resp = FileResponse(grid_out, content_type=grid_out.content_type or "image/jpeg")
        resp["Content-Length"] = grid_out.length
        return resp
    except Exception:
        pass
    path = os.path.join(_contact_media_dir(), safe_name)
    if not os.path.exists(path):
        raise Http404("Imagen no encontrada")
    return FileResponse(open(path, "rb"))


def directory_person_image(request, filename):
    if not (getattr(request.user, "is_authenticated", False) or _require_api_key(request)):
        return JsonResponse({"error": "Unauthorized"}, status=401)
    safe_name = os.path.basename(str(filename or ""))
    try:
        grid_out = fs_people.get_last_version(filename=safe_name)
        resp = FileResponse(grid_out, content_type=grid_out.content_type or "image/jpeg")
        resp["Content-Length"] = grid_out.length
        return resp
    except Exception:
        pass
    path = os.path.join(_directory_media_dir(), safe_name)
    if not os.path.exists(path):
        raise Http404("Imagen no encontrada")
    return FileResponse(open(path, "rb"))


def _serialize_usernames_text(raw_usernames):
    return "\n".join(normalize_username_list(raw_usernames))


def _publish_contacts_sync(target, contacts=None, request=None):
    queue_payload = {
        "type": "contacts_updated",
        "to": target,
        "from": "cobien-admin",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    normalized_contacts = normalize_contacts_list(contacts or [])
    if normalized_contacts:
        queue_payload["contacts"] = normalized_contacts
    if request is not None:
        try:
            queue_payload["contacts_url"] = request.build_absolute_uri(
                f"{reverse('pizarra_api_contacts')}?device_id={target}"
            )
        except Exception:
            pass
    enqueue_notification(target, queue_payload)
    return queue_payload


@login_required
def pizarra_home(request):
    contacts_server = set()
    is_admin = _staff_required(request.user)
    accessible_devices = get_accessible_device_ids(
        username=getattr(request.user, "username", ""),
        email=getattr(request.user, "email", ""),
    )
    linked_device = get_default_device_id(
        username=getattr(request.user, "username", ""),
        email=getattr(request.user, "email", ""),
    )
    for device_id in accessible_devices:
        contacts_server.add(device_id)
        for item in get_device_contacts(device_id):
            contacts_server.add(item["display_name"])
            contacts_server.add(item["user_name"])

    # Destinatarios ya usados por este usuario
    prev = col_messages.aggregate([
        {"$match": {"author": request.user.username}},
        {"$group": {"_id": "$recipient_key"}},
    ])
    contacts_server.update([d["_id"] for d in prev if d.get("_id")])

    if linked_device:
        contacts_server.add(linked_device)

    selected_contact = (request.GET.get("to") or linked_device or "").strip()

    # Build device cards for the visual selector
    _all_known = {
        str(d.get("device_id") or "").strip(): d
        for d in list_known_devices()
        if str(d.get("device_id") or "").strip()
    }
    if is_admin:
        visible_devices = [d for d in _all_known.values() if not d.get("hidden_in_admin")]
    else:
        visible_devices = [_all_known[did] for did in accessible_devices if did in _all_known]

    device_cards = []
    for device in visible_devices:
        did = str(device.get("device_id") or "").strip()
        if not did:
            continue
        device_cards.append({
            "device_id": did,
            "display_name": str(device.get("display_name") or did).strip() or did,
            "color": _color_for_device(did),
            "status": device_online_status(device),
            "is_selected": did == selected_contact,
        })

    # Histórico
    posts = []
    if selected_contact:
        message_filter = {"recipient_key": selected_contact}
        if not is_admin:
            message_filter["author"] = request.user.username
        cursor = col_messages.find(message_filter).sort("created_at", DESCENDING)
        for d in cursor:
            image_url = ""
            if d.get("image_file_id"):
                image_url = request.build_absolute_uri(
                    reverse("pizarra_image", args=[str(d["image_file_id"])])
                )
            author_meta = _build_message_author_meta(d.get("author"), request=request)
            posts.append({
                "id": str(d["_id"]),
                "recipient_key": d.get("recipient_key"),
                "content": d.get("content", ""),
                "image_url": image_url,
                "created_at": d.get("created_at"),
                "created_at_human": fecha_chat(d.get("created_at")),
                **author_meta,
            })

    # --- Inbox de notificaciones para el usuario web ---
    notifications_filter = {"read": False}
    if not is_admin:
        notifications_filter["to_user"] = request.user.username

    notifs_cursor = col_notifications.find(notifications_filter).sort("created_at", DESCENDING).limit(100)

    notifications = []
    for d in notifs_cursor:
        notifications.append({
            "id": str(d["_id"]),
            "from_device": d.get("from_device") or d.get("from") or "",
            "to_user": d.get("to_user") or "",
            "kind": d.get("kind", "call_ready"),
            "message": d.get("message", "Disponible para llamada"),
            "created_at": d.get("created_at"),
            "read": d.get("read", False),
        })

    unread_count = len(notifications)

    form = PizarraPostForm(initial={"recipient_key": selected_contact})
    ctx = {
        "selected_contact": selected_contact,
        "linked_device": linked_device,
        "contacts": sorted(contacts_server, key=str.casefold),
        "device_cards": device_cards,
        "posts": posts,
        "form": form,
        "notifications": notifications,
        "unread_count": unread_count,
    }
    return render(request, "pizarra/pizarra.html", ctx)


def _payload_items(payload: dict) -> list:
    items = []
    if not isinstance(payload, dict):
        return items
    for k, v in payload.items():
        if isinstance(v, (dict, list)):
            items.append({"key": k, "value": json.dumps(v, indent=2, default=str, ensure_ascii=False), "complex": True})
        elif v is None:
            items.append({"key": k, "value": "—", "complex": False})
        else:
            items.append({"key": k, "value": str(v), "complex": False})
    return items


@login_required
@user_passes_test(_staff_required)
def icso_dashboard(request):
    selected_device = (request.GET.get("device_id") or "").strip()
    device_docs = list(
        col_icso_snapshots.find({}, {"device_id": 1}).sort("device_id", ASCENDING)
    )
    devices = [str(doc.get("device_id") or "").strip() for doc in device_docs if str(doc.get("device_id") or "").strip()]
    if not selected_device and devices:
        selected_device = devices[0]

    # Build device cards enriched with display_name and online status
    device_cards = []
    for did in devices:
        doc = get_or_create_device(did)
        if doc:
            device_cards.append({
                "device_id": did,
                "display_name": str(doc.get("display_name") or did).strip() or did,
                "status": device_online_status(doc),
                "is_selected": did == selected_device,
            })

    snapshot = None
    events = []
    sources = []
    if selected_device:
        snapshot_doc = col_icso_snapshots.find_one({"device_id": selected_device})
        if snapshot_doc:
            payload = snapshot_doc.get("payload", {})
            snapshot = {
                "device_id": snapshot_doc.get("device_id"),
                "updated_at": _serialize_datetime(snapshot_doc.get("updated_at")),
                "captured_at": _serialize_datetime(snapshot_doc.get("captured_at")),
                "payload_items": _payload_items(payload),
                "payload_json": json.dumps(payload, indent=2, default=str, ensure_ascii=False),
                "payload_count": len(payload) if isinstance(payload, dict) else 0,
            }

        cursor = col_icso_events.find({"device_id": selected_device}).sort("logged_at", DESCENDING).limit(100)
        for doc in cursor:
            item = _serialize_doc(doc)
            src = item.get("source", "") or "icso"
            events.append({
                "source": src,
                "logged_at": item.get("logged_at") or item.get("created_at") or "",
                "message": item.get("message", ""),
            })
            if src not in sources:
                sources.append(src)

    return render(
        request,
        "pizarra/icso_dashboard.html",
        {
            "devices": devices,
            "device_cards": device_cards,
            "selected_device": selected_device,
            "snapshot": snapshot,
            "events": events,
            "sources": sources,
            "events_count": len(events),
        },
    )


@login_required
@user_passes_test(_staff_required)
def icso_download_events(request):
    device_id = (request.GET.get("device_id") or "").strip()
    fmt = (request.GET.get("format") or "csv").strip().lower()
    if not device_id:
        return HttpResponse("Falta el parámetro device_id.", status=400, content_type="text/plain")

    cursor = col_icso_events.find({"device_id": device_id}).sort("logged_at", DESCENDING).limit(5000)
    events = []
    for doc in cursor:
        item = _serialize_doc(doc)
        events.append({
            "device_id": device_id,
            "source": item.get("source", "") or "icso",
            "logged_at": item.get("logged_at") or item.get("created_at") or "",
            "message": item.get("message", ""),
        })

    safe_device = re.sub(r"[^\w\-]", "_", device_id)

    if fmt == "json":
        content = json.dumps(events, indent=2, default=str, ensure_ascii=False)
        response = HttpResponse(content, content_type="application/json; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="icso_events_{safe_device}.json"'
        return response

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["device_id", "source", "logged_at", "message"])
    writer.writeheader()
    writer.writerows(events)
    response = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="icso_events_{safe_device}.csv"'
    return response


@login_required
@user_passes_test(_staff_required)
def icso_download_snapshot(request):
    device_id = (request.GET.get("device_id") or "").strip()
    if not device_id:
        return HttpResponse("Falta el parámetro device_id.", status=400, content_type="text/plain")

    snapshot_doc = col_icso_snapshots.find_one({"device_id": device_id})
    if not snapshot_doc:
        return HttpResponse("No hay snapshot para este dispositivo.", status=404, content_type="text/plain")

    data = {
        "device_id": snapshot_doc.get("device_id"),
        "captured_at": _serialize_datetime(snapshot_doc.get("captured_at")),
        "updated_at": _serialize_datetime(snapshot_doc.get("updated_at")),
        "payload": snapshot_doc.get("payload", {}),
    }
    content = json.dumps(data, indent=2, default=str, ensure_ascii=False)
    safe_device = re.sub(r"[^\w\-]", "_", device_id)
    response = HttpResponse(content, content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="icso_snapshot_{safe_device}.json"'
    return response


@login_required
@user_passes_test(_staff_required)
def device_delete(request):
    """Dedicated endpoint for device deletion — POST only, returns JSON."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    device_id = (request.POST.get("device_id") or "").strip()
    if not device_id:
        return JsonResponse({"error": "device_id requerido"}, status=400)
    try:
        col_messages.delete_many({"recipient_key": device_id})
        col_notifications.delete_many({"$or": [{"to_user": device_id}, {"from_device": device_id}]})
        db["pizarra_device_queue"].delete_many({"device_id": device_id})
        col_icso_snapshots.delete_many({"device_id": device_id})
        col_icso_events.delete_many({"device_id": device_id})
        col_user_device_access.delete_many({"device_id": device_id})
        col_devices.delete_one({"device_id": device_id})
        _ref_filter = {"$or": [
            {"target_device": device_id},
            {"default_room": device_id},
            {"linked_device": device_id},
        ]}
        _ref_unset = {"$unset": {"target_device": "", "default_room": "", "linked_device": ""}}
        for _colname in ("auth_user", "users"):
            try:
                db[_colname].update_many(_ref_filter, _ref_unset)
            except Exception:
                pass
        return JsonResponse({"ok": True, "device_id": device_id})
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


@login_required
@user_passes_test(_staff_required)
def devices_admin(request):
    if request.method == "POST":
        action = (request.POST.get("action") or "create").strip()
        selected_device = (request.POST.get("device_id") or "").strip()
        try:
            if action in {"save", "sync", "save_and_sync"}:
                if not selected_device:
                    raise ValueError("Selecciona un dispositivo.")
                form = DeviceContactsAdminForm(request.POST)
                if not form.is_valid():
                    raise ValueError(" ".join([str(err) for errors in form.errors.values() for err in errors]))
                cleaned = form.cleaned_data
                device_doc = get_or_create_device(selected_device) or {}
                contacts = []
                deployment_profile = _parse_deployment_profile_json(cleaned.get("deployment_profile_json", ""))
                if action in {"save", "save_and_sync"}:
                    contacts = _parse_contact_rows(request, selected_device, device_doc.get("contacts", []))
                    display_name = cleaned.get("display_name") or selected_device
                    assigned_users = normalize_username_list(str(cleaned.get("assigned_users", "")).splitlines())
                    default_username = cleaned.get("default_username", "")
                    update_device_contacts(selected_device, contacts, display_name=display_name)
                    update_device_metadata(
                        selected_device,
                        display_name=display_name,
                        videocall_room=cleaned.get("videocall_room", ""),
                        enabled=cleaned.get("enabled", False),
                        hidden_in_admin=cleaned.get("hidden_in_admin", False),
                        event_visibility_scope=cleaned.get("event_visibility_scope", "all"),
                        event_regions=cleaned.get("event_regions", ""),
                        deployment_profile=deployment_profile,
                    )
                    replace_device_assignments(selected_device, assigned_users, default_username=default_username)
                    if action == "save":
                        messages.success(request, f"Configuración guardada para {selected_device}.")
                if action in {"save", "sync", "save_and_sync"}:
                    sync_contacts = contacts or normalize_contacts_list((get_or_create_device(selected_device) or {}).get("contacts", []))
                    _publish_contacts_sync(selected_device, contacts=sync_contacts, request=request)
                    if action == "sync":
                        messages.success(request, f"Sincronización enviada a {selected_device}.")
                    elif action == "save_and_sync":
                        messages.success(request, f"Configuración y sincronización enviadas a {selected_device}.")
            elif action == "delete":
                if not selected_device:
                    raise ValueError("Selecciona un dispositivo para eliminar.")
                col_messages.delete_many({"recipient_key": selected_device})
                col_notifications.delete_many({"$or": [{"to_user": selected_device}, {"from_device": selected_device}]})
                db["pizarra_device_queue"].delete_many({"device_id": selected_device})
                col_icso_snapshots.delete_many({"device_id": selected_device})
                col_icso_events.delete_many({"device_id": selected_device})
                col_device_runtime_logs.delete_many({"device_id": selected_device})
                col_user_device_access.delete_many({"device_id": selected_device})
                col_devices.delete_one({"device_id": selected_device})
                # Remove device references from user profiles so list_known_devices()
                # doesn't rediscover and recreate the device via get_or_create_device().
                _ref_filter = {"$or": [
                    {"target_device": selected_device},
                    {"default_room": selected_device},
                    {"linked_device": selected_device},
                ]}
                _ref_unset = {"$unset": {"target_device": "", "default_room": "", "linked_device": ""}}
                for _colname in ("auth_user", "users"):
                    try:
                        db[_colname].update_many(_ref_filter, _ref_unset)
                    except Exception:
                        pass
                messages.success(request, f"Mueble '{selected_device}' y todos sus datos eliminados.")
                return redirect(reverse("pizarra_devices_admin"))
            else:
                form = DeviceAdminForm(request.POST)
                if not form.is_valid():
                    raise ValueError(" ".join([str(err) for errors in form.errors.values() for err in errors]))
                cleaned = form.cleaned_data
                deployment_profile = _parse_deployment_profile_json(cleaned.get("deployment_profile_json", ""))
                if action == "create":
                    create_device(
                        cleaned["device_id"],
                        display_name=cleaned.get("display_name", ""),
                        videocall_room=cleaned.get("videocall_room", ""),
                        enabled=cleaned.get("enabled", False),
                        hidden_in_admin=cleaned.get("hidden_in_admin", False),
                        event_visibility_scope=cleaned.get("event_visibility_scope", "all"),
                        event_regions=cleaned.get("event_regions", ""),
                        deployment_profile=deployment_profile,
                    )
                    selected_device = cleaned["device_id"]
                    messages.success(request, "Dispositivo creado.")
                elif action == "update":
                    update_device_metadata(
                        cleaned["device_id"],
                        display_name=cleaned.get("display_name", ""),
                        videocall_room=cleaned.get("videocall_room", ""),
                        enabled=cleaned.get("enabled", False),
                        hidden_in_admin=cleaned.get("hidden_in_admin", False),
                        event_visibility_scope=cleaned.get("event_visibility_scope", "all"),
                        event_regions=cleaned.get("event_regions", ""),
                        deployment_profile=deployment_profile,
                    )
                    selected_device = cleaned["device_id"]
                    messages.success(request, "Dispositivo actualizado.")
                else:
                    messages.error(request, "Acción no soportada.")
        except Exception as exc:
            messages.error(request, str(exc))
        redirect_url = reverse("pizarra_devices_admin")
        if selected_device:
            redirect_url = f"{redirect_url}?device_id={selected_device}"
        return redirect(redirect_url)

    show_hidden = request.GET.get("show_hidden", "0") in ("1", "true", "True")
    selected_device = (request.GET.get("device_id") or "").strip()
    return render(request, "pizarra/devices_admin.html", _build_device_management_context(selected_device, show_hidden=show_hidden))


def api_admin_devices_list(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    user = _authenticate_staff_from_request(request)
    if not user:
        return _admin_api_auth_failed_response()

    devices = []
    for item in list_known_devices():
        devices.append(
            {
                "device_id": str(item.get("device_id") or "").strip(),
                "display_name": str(item.get("display_name") or item.get("device_id") or "").strip(),
                "videocall_room": str(item.get("videocall_room") or item.get("device_id") or "").strip(),
                "enabled": bool(item.get("enabled", True)),
                "hidden_in_admin": bool(item.get("hidden_in_admin", False)),
                "status": device_online_status(item),
            }
        )
    devices.sort(key=lambda item: (not item["enabled"], item["hidden_in_admin"], item["display_name"].casefold()))
    return JsonResponse({"ok": True, "devices": devices})


def api_admin_device_env(request, device_id):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    user = _authenticate_staff_from_request(request)
    if not user:
        return _admin_api_auth_failed_response()

    selected_device = str(device_id or "").strip()
    if not selected_device:
        return HttpResponse("Missing device_id.", status=400, content_type="text/plain; charset=utf-8")

    device_doc = get_or_create_device(selected_device)
    if not device_doc:
        return HttpResponse("Device not found.", status=404, content_type="text/plain; charset=utf-8")

    content = _serialize_cobien_env(_build_cobien_env_map(device_doc, request=request))
    response = HttpResponse(content, content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="cobien.env.{selected_device}"'
    return response


@login_required
def my_profile(request):
    User = get_user_model()
    user = request.user
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        try:
            if action == "update_profile":
                first_name = str(request.POST.get("first_name") or "").strip()
                last_name  = str(request.POST.get("last_name") or "").strip()
                email      = str(request.POST.get("email") or "").strip().lower()
                remove_image = request.POST.get("remove_image") == "1"
                uploaded_file = request.FILES.get("image")
                user.first_name = first_name
                user.last_name  = last_name
                user.email      = email
                user.save()
                if remove_image:
                    _gridfs_delete_by_filename(fs_people, "pizarra_people_fs", _user_avatar_filename(user.username))
                if uploaded_file:
                    _save_user_avatar(user.username, uploaded_file)
                messages.success(request, "Profile updated.")
            elif action == "change_password":
                new_password = str(request.POST.get("new_password") or "").strip()
                confirm      = str(request.POST.get("confirm_password") or "").strip()
                if not new_password or len(new_password) < 6:
                    raise ValueError("Password must be at least 6 characters.")
                if new_password != confirm:
                    raise ValueError("Passwords do not match.")
                from django.contrib.auth import update_session_auth_hash
                user.set_password(new_password)
                user.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Password updated.")
            elif action == "delete_account":
                if _staff_required(user):
                    raise ValueError("Administrators cannot delete their own account from this screen.")
                username = str(user.username or "").strip()
                email = str(user.email or "").strip()
                _gridfs_delete_by_filename(fs_people, "pizarra_people_fs", _user_avatar_filename(username))
                if username:
                    col_user_device_access.delete_many({"username": username})
                    col_notifications.delete_many({"to_user": username})
                    col_messages.delete_many({"author": username})
                    db["auth_user"].delete_many({"username": username})
                    db["users"].delete_many({"username": username})
                    if email:
                        db["auth_user"].delete_many({"email": email})
                        db["users"].delete_many({"email": email})
                from django.contrib.auth import logout
                user.delete()
                logout(request)
                messages.success(request, "Your account has been deleted.")
                return redirect("/")
        except Exception as exc:
            messages.error(request, str(exc))
        return redirect(reverse("my_profile"))

    image_url = _user_avatar_url(user.username)
    context = {
        "profile_user": user,
        "image_url": image_url,
    }
    return render(request, "pizarra/my_profile.html", context)


@login_required
@user_passes_test(_staff_required)
def directory_people_admin(request):
    User = get_user_model()
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        try:
            if action == "create_user":
                username = str(request.POST.get("username") or "").strip().lower()
                email = str(request.POST.get("email") or "").strip().lower()
                first_name = str(request.POST.get("first_name") or "").strip()
                last_name = str(request.POST.get("last_name") or "").strip()
                is_staff = request.POST.get("is_staff") == "1"
                generate_password = request.POST.get("generate_password") == "1"
                send_email_flag = request.POST.get("send_email") == "1"
                if not username:
                    raise ValueError("El nombre de usuario es obligatorio.")
                if generate_password:
                    alphabet = string.ascii_letters + string.digits + "!@#$%&*"
                    password = ''.join(secrets.choice(alphabet) for _ in range(14))
                else:
                    password = str(request.POST.get("password") or "").strip()
                    if not password or len(password) < 6:
                        raise ValueError("La contraseña debe tener al menos 6 caracteres.")
                if db["auth_user"].find_one({"username": username}):
                    raise ValueError(f"El usuario '{username}' ya existe.")
                user = User(username=username, email=email, first_name=first_name,
                            last_name=last_name, is_active=True, is_staff=is_staff)
                user.set_password(password)
                user.save()
                mongo_update = {"email_verified": True, "preferred_language": "es", "is_admin": is_staff}
                if generate_password:
                    mongo_update["must_change_password"] = True
                db["auth_user"].update_one(
                    {"username": username},
                    {"$set": mongo_update},
                    upsert=True,
                )
                uploaded_file = request.FILES.get("image")
                if uploaded_file:
                    _save_user_avatar(username, uploaded_file)
                if generate_password and send_email_flag and email:
                    _send_provisional_password_email(request, user, password)
                messages.success(request, f"Usuario '{username}' creado correctamente.")

            elif action == "update_user":
                username = str(request.POST.get("username") or "").strip()
                if not username:
                    raise ValueError("Username requerido.")
                first_name = str(request.POST.get("first_name") or "").strip()
                last_name = str(request.POST.get("last_name") or "").strip()
                email = str(request.POST.get("email") or "").strip().lower()
                is_staff = request.POST.get("is_staff") == "1"
                is_active = request.POST.get("is_active") != "0"
                remove_image = request.POST.get("remove_image") == "1"
                uploaded_file = request.FILES.get("image")
                user = User.objects.get(username=username)
                user.first_name = first_name
                user.last_name = last_name
                user.email = email
                user.is_staff = is_staff
                user.is_active = is_active
                user.save()
                db["auth_user"].update_one(
                    {"username": username},
                    {"$set": {"is_admin": is_staff}},
                )
                if remove_image:
                    _gridfs_delete_by_filename(fs_people, "pizarra_people_fs", _user_avatar_filename(username))
                if uploaded_file:
                    _save_user_avatar(username, uploaded_file)
                messages.success(request, f"Usuario '{username}' actualizado.")

            elif action == "set_password":
                username = str(request.POST.get("username") or "").strip()
                password = str(request.POST.get("new_password") or "").strip()
                if not username:
                    raise ValueError("Username requerido.")
                if not password or len(password) < 6:
                    raise ValueError("La contraseña debe tener al menos 6 caracteres.")
                user = User.objects.get(username=username)
                user.set_password(password)
                user.save()
                messages.success(request, f"Contraseña de '{username}' actualizada.")

            elif action == "delete_user":
                username = str(request.POST.get("username") or "").strip()
                if not username:
                    raise ValueError("Username requerido.")
                if username == request.user.username:
                    raise ValueError("No puedes eliminar tu propia cuenta.")
                User.objects.filter(username=username).delete()
                _gridfs_delete_by_filename(fs_people, "pizarra_people_fs", _user_avatar_filename(username))
                messages.success(request, f"Usuario '{username}' eliminado.")

        except Exception as exc:
            messages.error(request, str(exc))
        return redirect(reverse("pizarra_directory_people_admin"))

    users = _list_users_for_admin()
    return render(request, "pizarra/directory_people_admin.html", {"people": users})


@login_required
@user_passes_test(_staff_required)
def device_contacts_admin(request):
    selected_device = (request.GET.get("device_id") or request.POST.get("device_id") or "").strip()
    target = reverse("pizarra_devices_admin")
    if selected_device:
        target = f"{target}?device_id={selected_device}"
    return redirect(target)


@login_required
def pizarra_delete(request, post_id: str):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        doc = col_messages.find_one({"_id": ObjectId(post_id), "author": request.user.username})
    except Exception:
        doc = None

    if not doc:
        messages.error(request, "Mensaje no encontrado.")
        return redirect("pizarra_home")

    recipient_key = doc.get("recipient_key", "")

    try:
        image_file_id = doc.get("image_file_id")
        if image_file_id:
            try:
                fs.delete(ObjectId(str(image_file_id)))
            except Exception:
                pass

        col_messages.delete_one({"_id": doc["_id"], "author": request.user.username})
        _enqueue_board_reload(recipient_key, show_last=False)
        messages.success(request, "Mensaje eliminado.")
    except Exception as e:
        messages.error(request, f"No se pudo eliminar el mensaje: {e}")

    target = reverse("pizarra_home")
    if recipient_key:
        target = f"{target}?to={recipient_key}"
    return redirect(target)

@login_required
def pizarra_create(request):
    if request.method != "POST":
        return redirect("pizarra_home")

    form = PizarraPostForm(request.POST, request.FILES)
    if not form.is_valid():
        to = request.POST.get("recipient_key", "")
        for err in form.errors.values():
            messages.error(request, err)
        return redirect(f"{reverse('pizarra_home')}?to={to}")

    cleaned = form.cleaned_data

    # Guarda imagen en GridFS (si hay)
    file_id = None
    img = cleaned.get("image")
    if img:
        file_id = fs.put(img.file, filename=img.name, contentType=getattr(img, "content_type", None))

    # Inserta documento
    doc = {
        "author": request.user.username,
        "recipient_key": cleaned["recipient_key"].strip(),
        "content": cleaned.get("content") or "",
        "image_file_id": file_id,
        "created_at": datetime.now(timezone.utc),
    }
    col_messages.insert_one(doc)

    try:
        image_url = (
            request.build_absolute_uri(reverse("pizarra_image", args=[str(file_id)]))
            if file_id else ""
        )
        payload = json.dumps({
            "type": "new_message",
            "from": request.user.username,
            "to": doc["recipient_key"],
            "text": doc.get("content", ""),
            "image_url": image_url,
            "timestamp": doc["created_at"].isoformat()
        })
        enqueue_notification(doc["recipient_key"], json.loads(payload))
    except Exception:
        pass

    messages.success(request, "¡Mensaje guardado!")
    return redirect(f"{reverse('pizarra_home')}?to={doc['recipient_key']}")


@login_required
def pizarra_web_messages(request):
    recipient = request.GET.get("recipient", "").strip()
    if not recipient:
        return JsonResponse({"ok": False, "error": "Missing recipient"}, status=400)
    message_filter = {"recipient_key": recipient}
    if not _staff_required(request.user):
        message_filter["author"] = request.user.username
    cursor = col_messages.find(message_filter).sort("created_at", DESCENDING).limit(50)
    posts = []
    for d in cursor:
        image_url = ""
        if d.get("image_file_id"):
            image_url = request.build_absolute_uri(
                reverse("pizarra_image", args=[str(d["image_file_id"])])
            )
        author_meta = _build_message_author_meta(d.get("author"), request=request)
        posts.append({
            "id": str(d["_id"]),
            "content": d.get("content", ""),
            "image_url": image_url,
            "created_at_human": fecha_chat(d.get("created_at")),
            **author_meta,
        })
    return JsonResponse({"ok": True, "posts": posts})


@login_required
def pizarra_send_multi(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Method not allowed"}, status=405)
    recipients = [r.strip() for r in request.POST.getlist("recipient_keys") if r.strip()]
    if not recipients:
        single = request.POST.get("recipient_key", "").strip()
        if single:
            recipients = [single]
    if not recipients:
        return JsonResponse({"ok": False, "error": "Falta el destinatario"}, status=400)
    content = request.POST.get("content", "").strip()
    img = request.FILES.get("image")
    if not content and not img:
        return JsonResponse({"ok": False, "error": "Escribe un mensaje o sube una imagen."}, status=400)
    file_id = None
    if img:
        file_id = fs.put(img.file, filename=img.name, contentType=getattr(img, "content_type", None))
    now = datetime.now(timezone.utc)
    for rk in recipients:
        doc = {
            "author": request.user.username,
            "recipient_key": rk,
            "content": content,
            "image_file_id": file_id,
            "created_at": now,
        }
        col_messages.insert_one(doc)
        try:
            image_url = (
                request.build_absolute_uri(reverse("pizarra_image", args=[str(file_id)]))
                if file_id else ""
            )
            enqueue_notification(rk, {
                "type": "new_message",
                "from": request.user.username,
                "to": rk,
                "text": content,
                "image_url": image_url,
                "timestamp": now.isoformat(),
            })
        except Exception as e:
            print(f"[PIZARRA] Enqueue error for {rk}: {e}")
    return JsonResponse({"ok": True, "count": len(recipients)})


@login_required
def pizarra_web_delete(request, post_id: str):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Method not allowed"}, status=405)
    try:
        target_id = ObjectId(post_id)
    except Exception:
        return JsonResponse({"ok": False, "error": "post_id inválido"}, status=400)
    delete_filter = {"_id": target_id}
    if not _staff_required(request.user):
        delete_filter["author"] = request.user.username
    doc = col_messages.find_one(delete_filter)
    if not doc:
        return JsonResponse({"ok": False, "error": "Mensaje no encontrado"}, status=404)
    if doc.get("image_file_id"):
        try:
            fs.delete(doc["image_file_id"])
        except Exception:
            pass
    col_messages.delete_one({"_id": target_id})
    _enqueue_board_reload(doc.get("recipient_key", ""), show_last=False)
    return JsonResponse({"ok": True, "id": post_id})


def fecha_chat(value):
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if not isinstance(value, datetime):
        return str(value)
    months = [
        "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    ]
    dt = value.astimezone(timezone.utc).astimezone()
    return f"{dt.day} de {months[dt.month]} a las {dt.strftime('%H:%M')}"

def pizarra_image(request, file_id: str):
    # Sirve la imagen almacenada en GridFS — accesible con sesión o X-API-KEY
    if not getattr(request.user, "is_authenticated", False) and not _require_api_key(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)
    try:
        grid_out = fs.get(ObjectId(file_id))
    except Exception:
        raise Http404("Imagen no encontrada.")

    resp = FileResponse(grid_out, content_type=grid_out.content_type or "application/octet-stream")
    resp["Content-Length"] = grid_out.length
    resp["Content-Disposition"] = f'inline; filename="{grid_out.filename}"'
    return resp

def api_pizarra_messages(request):
    """
    Endpoint para la app del mueble (si quieres, protégelo con un token simple).
    GET:
      - recipient (obligatorio)
      - since (ISO8601, opcional) -> devuelve solo posteriores
    """
    recipient = (request.GET.get("recipient") or "").strip()
    if not recipient:
        return JsonResponse({"error": "recipient requerido"}, status=400)

    filt = {"recipient_key": recipient}
    since = request.GET.get("since")
    if since:
        try:
            # admite ...Z o con offset
            dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            filt["created_at"] = {"$gt": dt}
        except Exception:
            pass

    cursor = col_messages.find(filt).sort("created_at", DESCENDING).limit(100)
    items = []
    for d in cursor:
        if d.get("deleted_from_device"):
            continue
        image_url = ""
        if d.get("image_file_id"):
            image_url = request.build_absolute_uri(
                reverse("pizarra_image", args=[str(d["image_file_id"])])
            )
        author_meta = _build_message_author_meta(d.get("author"), request=request)
        items.append({
            "id": str(d["_id"]),
            "author": author_meta["author"],
            "author_name": author_meta["author_name"],
            "author_avatar_url": author_meta["author_avatar_url"],
            "recipient": d.get("recipient_key"),
            "text": d.get("content", ""),
            "image": image_url,
            "image_url": image_url,
            "created_at": d.get("created_at").isoformat(),
            "created_at_human": fecha_chat(d.get("created_at")),
        })

    return JsonResponse({"messages": items})


@csrf_exempt
def api_delete_pizarra_message(request, post_id: str):
    if request.method not in ("POST", "DELETE"):
        return JsonResponse({"error": "Método no permitido. Usa POST o DELETE."}, status=405)

    api_key = request.headers.get("X-API-KEY") or request.POST.get("api_key")
    if getattr(settings, "NOTIFY_API_KEY", ""):
        if api_key != settings.NOTIFY_API_KEY:
            return JsonResponse({"error": "Unauthorized"}, status=401)

    try:
        target_id = ObjectId(post_id)
    except Exception:
        return JsonResponse({"error": "post_id inválido"}, status=400)

    doc = col_messages.find_one({"_id": target_id})
    if not doc:
        return JsonResponse({"error": "Mensaje no encontrado"}, status=404)

    delete_source = (
        request.headers.get("X-DELETE-SOURCE")
        or request.POST.get("source")
        or request.GET.get("source")
        or ""
    ).strip().lower()

    if delete_source == "device":
        ok = _mark_message_deleted_from_device(doc)
        return JsonResponse({"ok": ok, "id": post_id, "mode": "soft_deleted_from_device"})

    if doc.get("image_file_id"):
        try:
            fs.delete(doc["image_file_id"])
        except Exception:
            pass

    col_messages.delete_one({"_id": target_id})
    _enqueue_board_reload(doc.get("recipient_key", ""), show_last=False)
    return JsonResponse({"ok": True, "id": post_id})

@csrf_exempt
def api_notify(request):
    """
    Endpoint que el MUEBLE llama para avisar a un usuario web.
    POST (JSON o form):
      - to_user   (obligatorio): username del usuario web
      - from_device (opcional): identificador del mueble/persona
      - kind      (opcional): 'call_ready' por defecto
      - message   (opcional): texto corto
      - ttl_hours (opcional): override del TTL por-notificación
    Autorización:
      - Cabecera X-API-KEY debe coincidir con settings.NOTIFY_API_KEY.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Método no permitido. Usa POST."}, status=405)

    api_key = request.headers.get("X-API-KEY") or request.POST.get("api_key")
    if getattr(settings, "NOTIFY_API_KEY", ""):
        if api_key != settings.NOTIFY_API_KEY:
            return JsonResponse({"error": "Unauthorized"}, status=401)

    # Admite form-data, x-www-form-urlencoded o JSON
    try:
        payload = request.POST.dict()
        if not payload:
            payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        payload = {}

    to_user = (payload.get("to_user") or "").strip()
    if not to_user:
        return JsonResponse({"error": "to_user requerido"}, status=400)

    from_device = (payload.get("from_device") or payload.get("from") or "").strip()
    kind = (payload.get("kind") or "call_ready").strip()
    message = (payload.get("message") or "Disponible para llamada").strip()
    ttl_hours = payload.get("ttl_hours")

    try:
        ttl_hours = int(ttl_hours) if ttl_hours is not None else int(getattr(settings, "NOTIFY_TTL_HOURS", 24))
    except Exception:
        ttl_hours = int(getattr(settings, "NOTIFY_TTL_HOURS", 24))

    now = datetime.now(timezone.utc)
    expire_at = now + timedelta(hours=ttl_hours) if ttl_hours and ttl_hours > 0 else None

    doc = {
        "to_user": to_user,          # username del usuario web
        "from_device": from_device,  # quién avisa (mueble/persona)
        "kind": kind,                # 'call_ready'
        "message": message,          # texto corto
        "created_at": now,
        "read": False,
    }
    if expire_at:
        doc["expire_at"] = expire_at

    res = col_notifications.insert_one(doc)
    return JsonResponse({"ok": True, "id": str(res.inserted_id)})


@csrf_exempt
def api_device_heartbeat(request):
    if request.method != "POST":
        return JsonResponse({"error": "Método no permitido. Usa POST."}, status=405)

    if not _require_api_key(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)

    payload = _read_api_payload(request)
    device_id = str(payload.get("device_id", "") or "").strip()
    if not device_id:
        return JsonResponse({"error": "device_id requerido"}, status=400)

    try:
        device = touch_device_heartbeat(device_id, payload=payload)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    return JsonResponse(
        {
            "ok": True,
            "device_id": device_id,
            "status": device_online_status(device),
            "last_seen_at": _serialize_datetime(device.get("last_seen_at")),
        }
    )


def api_contacts_for_device(request):
    """
    Endpoint for furniture contact synchronization.
    GET params:
      - device_id (required)
    Auth:
      - X-API-KEY must match settings.NOTIFY_API_KEY when configured.
    """
    if request.method != "GET":
        return JsonResponse({"error": "Método no permitido. Usa GET."}, status=405)

    if not _require_api_key(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)

    device_id = (request.GET.get("device_id") or "").strip()
    if not device_id:
        return JsonResponse({"error": "device_id requerido"}, status=400)

    contacts = _contacts_for_api(get_device_contacts(device_id), request=request)
    if not contacts:
        return JsonResponse({"device_id": device_id, "contacts": []})

    return JsonResponse({"device_id": device_id, "contacts": contacts})


@csrf_exempt
def api_trigger_contacts_sync(request):
    """
    Trigger a contacts refresh on one furniture or all furniture devices.
    POST JSON/form:
      - to / target_device / recipient (required, supports "all")
    Auth:
      - X-API-KEY must match settings.NOTIFY_API_KEY when configured.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Método no permitido. Usa POST."}, status=405)

    if not _require_api_key(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)

    try:
        payload = request.POST.dict()
        if not payload:
            payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        payload = {}

    target = (
        payload.get("to")
        or payload.get("target_device")
        or payload.get("recipient")
        or ""
    ).strip()
    if not target:
        return JsonResponse({"error": "to/target_device requerido"}, status=400)

    queue_payload = {
        "type": "contacts_updated",
        "to": target,
        "from": payload.get("from") or "cobien",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        enqueue_notification(target, queue_payload)
    except Exception as exc:
        return JsonResponse({"error": f"Queue enqueue failed: {exc}"}, status=502)

    return JsonResponse({"ok": True, "published": queue_payload})


@csrf_exempt
def api_device_poll(request):
    if request.method != "GET":
        return JsonResponse({"error": "Método no permitido. Usa GET."}, status=405)

    if not _require_api_key(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)

    device_id = (request.GET.get("device_id") or "").strip()
    if not device_id:
        return JsonResponse({"error": "device_id requerido"}, status=400)

    limit = request.GET.get("limit", 50)
    try:
        notifications = claim_pending_notifications(device_id, limit=limit)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    return JsonResponse({
        "ok": True,
        "device_id": device_id,
        "notifications": notifications,
        "count": len(notifications),
    })


@csrf_exempt
def api_device_delivery_diagnostic(request):
    """
    Enqueue a diagnostic message targeted to one furniture device.
    Useful to verify the full backend -> device delivery path.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Método no permitido. Usa POST."}, status=405)

    if not _require_api_key(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)

    payload = _read_api_payload(request)
    target = (
        payload.get("to")
        or payload.get("target_device")
        or payload.get("recipient")
        or ""
    ).strip()
    if not target:
        return JsonResponse({"error": "to/target_device requerido"}, status=400)

    queue_payload = {
        "type": "backend_delivery_diagnostic",
        "to": target,
        "from": payload.get("from") or "cobien-admin",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "check_id": str(payload.get("check_id") or uuid.uuid4().hex),
    }

    try:
        enqueue_notification(target, queue_payload)
    except Exception as exc:
        return JsonResponse({"error": f"Queue enqueue failed: {exc}"}, status=502)

    return JsonResponse({"ok": True, "published": queue_payload})


@csrf_exempt
def api_device_logs_ingest(request):
    if request.method != "POST":
        return JsonResponse({"error": "Método no permitido. Usa POST."}, status=405)

    if not _require_api_key(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)

    payload = _read_api_payload(request)
    device_id = str(payload.get("device_id", "") or "").strip()
    if not device_id:
        return JsonResponse({"error": "device_id requerido"}, status=400)

    raw_logs = payload.get("logs", [])
    if not isinstance(raw_logs, list) or not raw_logs:
        return JsonResponse({"error": "logs requerido"}, status=400)

    stored = []
    rejected = []
    now = datetime.now(timezone.utc)
    for item in raw_logs:
        if not isinstance(item, dict):
            rejected.append({"reason": "invalid_item"})
            continue

        log_type = _normalize_device_runtime_log_type(item.get("log_type"))
        log_date = _normalize_device_runtime_log_date(item.get("log_date"))
        if not log_type or not log_date:
            rejected.append({"reason": "invalid_type_or_date", "log_type": item.get("log_type"), "log_date": item.get("log_date")})
            continue

        content = str(item.get("content") or "")
        filename = str(item.get("filename") or "").strip()
        line_count = int(item.get("line_count") or 0)
        byte_count = int(item.get("byte_count") or len(content.encode("utf-8", errors="ignore")))
        truncated = bool(item.get("truncated"))

        doc = {
            "device_id": device_id,
            "log_type": log_type,
            "log_date": log_date,
            "filename": filename,
            "content": content,
            "line_count": line_count,
            "byte_count": byte_count,
            "truncated": truncated,
            "updated_at": now,
            "sent_at": _parse_datetime_value(item.get("sent_at"), fallback=now) or now,
        }
        col_device_runtime_logs.update_one(
            {"device_id": device_id, "log_type": log_type, "log_date": log_date},
            {"$set": doc, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        stored.append({"log_type": log_type, "log_date": log_date, "filename": filename})

    return JsonResponse(
        {
            "ok": True,
            "device_id": device_id,
            "stored": stored,
            "stored_count": len(stored),
            "rejected": rejected,
            "rejected_count": len(rejected),
        }
    )

@login_required
def api_notifications(request):
    only_unread = request.GET.get("only_unread", "1") not in ("0", "false", "False")
    from_device = request.GET.get("from_device", "").strip()
    filt = {}
    if not _staff_required(request.user):
        accessible = get_accessible_device_ids(username=request.user.username)
        filt["to_user"] = request.user.username
        if accessible:
            filt["from_device"] = {"$in": accessible}
    if only_unread:
        filt["read"] = False
    if from_device:
        filt["from_device"] = from_device

    cursor = col_notifications.find(filt).sort("created_at", DESCENDING).limit(100)
    items = []
    for d in cursor:
        items.append({
            "id": str(d["_id"]),
            "from_device": d.get("from_device"),
            "to_user": d.get("to_user"),
            "kind": d.get("kind"),
            "message": d.get("message"),
            "created_at": d.get("created_at").isoformat(),
            "read": d.get("read", False),
        })

    return JsonResponse({"notifications": items})


@csrf_exempt
def api_icso_telemetry(request):
    if request.method == "POST":
        if not _require_api_key(request):
            return JsonResponse({"error": "Unauthorized"}, status=401)

        payload = _read_api_payload(request)
        device_id = str(payload.get("device_id", "") or "").strip()
        snapshot = payload.get("snapshot")
        if not device_id:
            return JsonResponse({"error": "device_id requerido"}, status=400)
        if not isinstance(snapshot, dict):
            return JsonResponse({"error": "snapshot requerido"}, status=400)

        now = datetime.now(timezone.utc)
        captured_at = _parse_datetime_value(payload.get("captured_at"), fallback=now)
        doc = {
            "device_id": device_id,
            "payload": snapshot,
            "captured_at": captured_at,
            "updated_at": now,
        }
        col_icso_snapshots.update_one(
            {"device_id": device_id},
            {"$set": doc},
            upsert=True,
        )
        return JsonResponse({"ok": True, "device_id": device_id})

    if request.method == "GET":
        if not _staff_required(request.user):
            return JsonResponse({"error": "Unauthorized"}, status=401)

        device_id = (request.GET.get("device_id") or "").strip()
        filt = {"device_id": device_id} if device_id else {}
        cursor = col_icso_snapshots.find(filt).sort("updated_at", DESCENDING).limit(100)
        items = [_serialize_doc(doc) for doc in cursor]
        return JsonResponse({"items": items})

    return JsonResponse({"error": "Método no permitido"}, status=405)


@csrf_exempt
def api_icso_events(request):
    if request.method == "POST":
        if not _require_api_key(request):
            return JsonResponse({"error": "Unauthorized"}, status=401)

        payload = _read_api_payload(request)
        device_id = str(payload.get("device_id", "") or "").strip()
        events = payload.get("events")
        if not device_id:
            return JsonResponse({"error": "device_id requerido"}, status=400)
        if not isinstance(events, list):
            return JsonResponse({"error": "events requerido"}, status=400)

        now = datetime.now(timezone.utc)
        docs = []
        for item in events:
            if not isinstance(item, dict):
                continue
            message = str(item.get("message", "") or "").strip()
            if not message:
                continue
            docs.append(
                {
                    "device_id": device_id,
                    "source": str(item.get("source", "") or "").strip() or "icso",
                    "logged_at": _parse_datetime_value(item.get("logged_at"), fallback=now),
                    "message": message,
                    "created_at": now,
                }
            )
        if docs:
            col_icso_events.insert_many(docs)
        return JsonResponse({"ok": True, "inserted": len(docs), "device_id": device_id})

    if request.method == "GET":
        if not _staff_required(request.user):
            return JsonResponse({"error": "Unauthorized"}, status=401)

        device_id = (request.GET.get("device_id") or "").strip()
        limit = min(max(int(request.GET.get("limit", 100) or 100), 1), 500)
        source = (request.GET.get("source") or "").strip()

        filt = {}
        if device_id:
            filt["device_id"] = device_id
        if source:
            filt["source"] = source

        cursor = col_icso_events.find(filt).sort("logged_at", DESCENDING).limit(limit)
        items = [_serialize_doc(doc) for doc in cursor]
        return JsonResponse({"items": items})

    return JsonResponse({"error": "Método no permitido"}, status=405)

@login_required
def notification_mark_read(request, notif_id: str):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    filt = {"_id": ObjectId(notif_id)}
    if not _staff_required(request.user):
        filt["to_user"] = request.user.username
    col_notifications.update_one(filt, {"$set": {"read": True, "read_at": datetime.now(timezone.utc)}})
    return redirect("pizarra_home")

@login_required
def notification_mark_all(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    filt = {"read": False}
    if not _staff_required(request.user):
        filt["to_user"] = request.user.username
    col_notifications.update_many(filt, {"$set": {"read": True, "read_at": datetime.now(timezone.utc)}})
    return redirect("pizarra_home")
