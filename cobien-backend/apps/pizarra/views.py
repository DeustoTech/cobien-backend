import os
import gridfs
import json
from bson import ObjectId
from datetime import datetime, timezone, timedelta
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.http import HttpResponseNotAllowed
from pymongo import MongoClient, DESCENDING, ASCENDING
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages
from django.urls import reverse
from django.http import FileResponse, Http404, JsonResponse
from .forms import PizarraPostForm
import paho.mqtt.publish as mqtt_publish

# --- Mongo / GridFS ---
_client = MongoClient(os.getenv("MONGO_URI"))
_dbname = os.getenv("DB_NAME", "LabasAppDB")
db = _client[_dbname]
fs = gridfs.GridFS(db, collection="pizarra_fs")
col_messages = db["pizarra_messages"]

# --- Notificaciones ---
col_notifications = db["pizarra_notifications"]
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


def _mqtt_auth():
    if settings.MQTT_USERNAME:
        return {
            "username": settings.MQTT_USERNAME,
            "password": settings.MQTT_PASSWORD,
        }
    return None

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

@login_required
def pizarra_home(request):
    # Perfil -> linked_device + contactos
    profile = None
    try:
        profile = _fetch_user_profile(request)
    except Exception:
        profile = None

    linked_device = None
    contacts_server = set()
    if profile:
        linked_device = (
            profile.get("target_device")
            or profile.get("default_room")
            or profile.get("linked_device")
        )
        raw_contacts = profile.get("contacts", [])
        if isinstance(raw_contacts, (list, tuple)):
            contacts_server.update([str(x).strip() for x in raw_contacts if str(x).strip()])

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
