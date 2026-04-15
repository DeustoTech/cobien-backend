import os
import gridfs
import json
import pprint
from bson import ObjectId
from datetime import datetime, timezone, timedelta
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.http import HttpResponseNotAllowed
from pymongo import MongoClient, DESCENDING, ASCENDING
from django.contrib.auth.decorators import login_required
from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import render, redirect
from django.contrib import messages
from django.urls import reverse
from django.http import FileResponse, Http404, JsonResponse
from .forms import PizarraPostForm
from .device_registry import (
    col_devices,
    col_user_device_access,
    get_accessible_device_ids,
    get_default_device_id,
    get_device_contacts,
    get_or_create_device,
    list_device_assignments,
    list_known_devices,
    normalize_contacts_list,
    replace_device_assignments,
    update_device_contacts,
)
import paho.mqtt.publish as mqtt_publish

# --- Mongo / GridFS ---
_client = MongoClient(os.getenv("MONGO_URI"))
_dbname = os.getenv("DB_NAME", "LabasAppDB")
db = _client[_dbname]
fs = gridfs.GridFS(db, collection="pizarra_fs")
col_messages = db["pizarra_messages"]

# --- Notificaciones ---
col_notifications = db["pizarra_notifications"]
col_icso_snapshots = db["pizarra_icso_snapshots"]
col_icso_events = db["pizarra_icso_events"]
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
except Exception:
    pass


def _mqtt_auth():
    if settings.MQTT_USERNAME:
        return {
            "username": settings.MQTT_USERNAME,
            "password": settings.MQTT_PASSWORD,
        }
    return None


def _require_api_key(request):
    expected = getattr(settings, "NOTIFY_API_KEY", "")
    if not expected:
        return True
    provided = request.headers.get("X-API-KEY") or request.GET.get("api_key") or request.POST.get("api_key")
    return provided == expected


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


def _publish_contacts_sync(target):
    mqtt_payload = {
        "type": "contacts_updated",
        "to": target,
        "from": "cobien-admin",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    mqtt_publish.single(
        topic=settings.MQTT_TOPIC_GENERAL,
        payload=json.dumps(mqtt_payload),
        hostname=settings.MQTT_BROKER_URL,
        port=settings.MQTT_BROKER_PORT,
        auth=_mqtt_auth(),
        qos=1,
    )
    return mqtt_payload


@login_required
def pizarra_home(request):
    contacts_server = set()
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

    # Histórico
    posts = []
    if selected_contact:
        cursor = col_messages.find(
            {"author": request.user.username, "recipient_key": selected_contact}
        ).sort("created_at", DESCENDING)
        for d in cursor:
            image_url = ""
            if d.get("image_file_id"):
                image_url = request.build_absolute_uri(
                    reverse("pizarra_image", args=[str(d["image_file_id"])])
                )
            posts.append({
                "id": str(d["_id"]),
                "recipient_key": d.get("recipient_key"),
                "content": d.get("content", ""),
                "image_url": image_url,
                "created_at": d.get("created_at"),
            })

    # --- Inbox de notificaciones para el usuario web ---
    notifs_cursor = col_notifications.find(
        {"to_user": request.user.username, "read": False}
    ).sort("created_at", DESCENDING).limit(50)

    notifications = []
    for d in notifs_cursor:
        notifications.append({
            "id": str(d["_id"]),
            "from_device": d.get("from_device") or d.get("from") or "",
            "kind": d.get("kind", "call_ready"),
            "message": d.get("message", "Disponible para llamada"),
            "created_at": d.get("created_at"),
        })

    unread_count = len(notifications)

    form = PizarraPostForm(initial={"recipient_key": selected_contact})
    ctx = {
        "selected_contact": selected_contact,
        "linked_device": linked_device,
        "contacts": sorted(contacts_server, key=str.casefold),
        "posts": posts,
        "form": form,
        "notifications": notifications,
        "unread_count": unread_count,
    }
    return render(request, "pizarra/pizarra.html", ctx)


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

    snapshot = None
    events = []
    if selected_device:
        snapshot_doc = col_icso_snapshots.find_one({"device_id": selected_device})
        if snapshot_doc:
            payload = snapshot_doc.get("payload", {})
            snapshot = {
                "device_id": snapshot_doc.get("device_id"),
                "updated_at": _serialize_datetime(snapshot_doc.get("updated_at")),
                "payload_pretty": pprint.pformat(payload, width=100, sort_dicts=True),
            }

        cursor = col_icso_events.find({"device_id": selected_device}).sort("logged_at", DESCENDING).limit(100)
        for doc in cursor:
            item = _serialize_doc(doc)
            events.append(
                {
                    "source": item.get("source", ""),
                    "logged_at": item.get("logged_at") or item.get("created_at") or "",
                    "message": item.get("message", ""),
                }
            )

    return render(
        request,
        "pizarra/icso_dashboard.html",
        {
            "devices": devices,
            "selected_device": selected_device,
            "snapshot": snapshot,
            "events": events,
        },
    )


@login_required
@user_passes_test(_staff_required)
def device_contacts_admin(request):
    device_ids = _list_known_device_ids()
    selected_device = (request.GET.get("device_id") or request.POST.get("device_id") or "").strip()
    if not selected_device and device_ids:
        selected_device = device_ids[0]

    device_doc = get_or_create_device(selected_device) if selected_device else None
    contacts_text = _serialize_contacts_text((device_doc or {}).get("contacts", []))
    profile_source = str((device_doc or {}).get("display_name") or "").strip()
    assignments = list_device_assignments(selected_device) if selected_device else []
    assigned_users_text = "\n".join(sorted([str(item.get("username") or "").strip() for item in assignments if str(item.get("username") or "").strip()], key=str.casefold))
    default_username = ""
    for item in assignments:
        if item.get("is_default"):
            default_username = str(item.get("username") or "").strip()
            break

    if request.method == "POST":
        action = (request.POST.get("action") or "save").strip()
        if not selected_device:
            messages.error(request, "Selecciona un dispositivo.")
            return redirect(reverse("pizarra_device_contacts_admin"))

        try:
            if action in {"save", "save_and_sync"}:
                contacts = _parse_contacts_text(request.POST.get("contacts", ""))
                display_name = (request.POST.get("display_name") or selected_device).strip()
                update_device_contacts(selected_device, contacts, display_name=display_name)
                assigned_users = [line.strip() for line in str(request.POST.get("assigned_users", "")).splitlines() if line.strip()]
                replace_device_assignments(
                    selected_device,
                    assigned_users,
                    default_username=(request.POST.get("default_username") or "").strip(),
                )
                messages.success(request, f"Contactos guardados para {selected_device}.")

            if action in {"sync", "save_and_sync"}:
                _publish_contacts_sync(selected_device)
                if action == "sync":
                    messages.success(request, f"Sincronización enviada a {selected_device}.")
                else:
                    messages.success(request, f"Sincronización enviada a {selected_device}.")
            elif action not in {"save", "save_and_sync"}:
                messages.error(request, "Acción no soportada.")
        except Exception as exc:
            messages.error(request, f"No se pudo actualizar la configuración: {exc}")

        return redirect(f"{reverse('pizarra_device_contacts_admin')}?device_id={selected_device}")

    return render(
        request,
        "pizarra/device_contacts_admin.html",
        {
            "device_ids": device_ids,
            "selected_device": selected_device,
            "contacts_text": contacts_text,
            "profile_source": profile_source,
            "assigned_users_text": assigned_users_text,
            "default_username": default_username,
        },
    )


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

    # ========== NOUVEAU : Envoyer notification MQTT au meuble ==========
    print(f"[MQTT PIZARRA] 🚀 Début envoi notification...")
    print(f"[MQTT PIZARRA]    From: {request.user.username}")
    print(f"[MQTT PIZARRA]    To: {doc['recipient_key']}")

    try:
        # ✅ Vérifier si settings existent
        print(f"[MQTT PIZARRA] 📋 Vérification settings:")
        
        broker_url = getattr(settings, 'MQTT_BROKER_URL', None)
        broker_port = getattr(settings, 'MQTT_BROKER_PORT', None)
        topic = getattr(settings, 'MQTT_TOPIC_GENERAL', None)
        
        print(f"[MQTT PIZARRA]    MQTT_BROKER_URL: {broker_url}")
        print(f"[MQTT PIZARRA]    MQTT_BROKER_PORT: {broker_port}")
        print(f"[MQTT PIZARRA]    MQTT_TOPIC_GENERAL: {topic}")
        
        if not broker_url or not broker_port or not topic:
            print(f"[MQTT PIZARRA] ❌ ERREUR: Settings MQTT manquants !")
            print(f"[MQTT PIZARRA]    Vérifier cobien_backend/settings.py")
            raise ValueError("Settings MQTT non configurés")
        
        payload = json.dumps({
            "type": "new_message",
            "from": request.user.username,
            "to": doc["recipient_key"],
            "text": doc.get("content", ""),
            "image": bool(file_id),
            "timestamp": doc["created_at"].isoformat()
        })
        
        print(f"[MQTT PIZARRA] 📦 Payload: {payload}")
        
        mqtt_publish.single(
            topic=topic,
            payload=payload,
            hostname=broker_url,
            port=broker_port,
            auth=_mqtt_auth(),
            qos=1
        )
        
        print(f"[MQTT PIZARRA] ✅ Notification envoyée avec succès !")
        
    except Exception as e:
        print(f"[MQTT PIZARRA] ❌ ERREUR: {e}")
        import traceback
        traceback.print_exc()

    print(f"[MQTT PIZARRA] 🏁 Fin envoi notification")
    # ===================================================================

    messages.success(request, "¡Mensaje guardado!")
    return redirect(f"{reverse('pizarra_home')}?to={doc['recipient_key']}")

@login_required
def pizarra_image(request, file_id: str):
    # Sirve la imagen almacenada en GridFS
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
        image_url = ""
        if d.get("image_file_id"):
            image_url = request.build_absolute_uri(
                reverse("pizarra_image", args=[str(d["image_file_id"])])
            )
        items.append({
            "id": str(d["_id"]),
            "author": d.get("author"),
            "recipient": d.get("recipient_key"),
            "text": d.get("content", ""),
            "image": image_url,
            "created_at": d.get("created_at").isoformat(),
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

    if doc.get("image_file_id"):
        try:
            fs.delete(doc["image_file_id"])
        except Exception:
            pass

    col_messages.delete_one({"_id": target_id})
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

    contacts = get_device_contacts(device_id)
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

    mqtt_payload = {
        "type": "contacts_updated",
        "to": target,
        "from": payload.get("from") or "cobien",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        mqtt_publish.single(
            topic=settings.MQTT_TOPIC_GENERAL,
            payload=json.dumps(mqtt_payload),
            hostname=settings.MQTT_BROKER_URL,
            port=settings.MQTT_BROKER_PORT,
            auth=_mqtt_auth(),
            qos=1,
        )
    except Exception as exc:
        return JsonResponse({"error": f"MQTT publish failed: {exc}"}, status=502)

    return JsonResponse({"ok": True, "published": mqtt_payload})

@login_required
def api_notifications(request):
    only_unread = request.GET.get("only_unread", "1") not in ("0", "false", "False")
    filt = {"to_user": request.user.username}
    if only_unread:
        filt["read"] = False

    cursor = col_notifications.find(filt).sort("created_at", DESCENDING).limit(100)
    items = []
    for d in cursor:
        items.append({
            "id": str(d["_id"]),
            "from_device": d.get("from_device"),
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

    col_notifications.update_one(
        {"_id": ObjectId(notif_id), "to_user": request.user.username},
        {"$set": {"read": True, "read_at": datetime.now(timezone.utc)}}
    )
    return redirect("pizarra_home")

@login_required
def notification_mark_all(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    col_notifications.update_many(
        {"to_user": request.user.username, "read": False},
        {"$set": {"read": True, "read_at": datetime.now(timezone.utc)}}
    )
    return redirect("pizarra_home")
