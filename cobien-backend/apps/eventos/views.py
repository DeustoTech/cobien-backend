from django.shortcuts import redirect, render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import Evento
from .serializers import EventoSerializer
from django.utils.translation import gettext as _
from django.utils import timezone
from datetime import datetime
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
import os
from django.conf import settings
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VideoGrant
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from functools import wraps
from pymongo import MongoClient
from bson import ObjectId
from django.urls import reverse
from rest_framework.permissions import IsAuthenticated
from .call_monitor import call_monitor
from apps.pizarra.device_queue import enqueue_broadcast_notification, enqueue_notification
from apps.pizarra.device_registry import (
    device_online_status,
    get_accessible_device_ids,
    get_default_device_id,
    get_device_videocall_context,
    get_or_create_device,
    list_known_devices,
    resolve_device_id_for_queue_target,
    verify_device_videocall_key,
)
_client = MongoClient(os.getenv("MONGO_URI"))
db = _client["LabasAppDB"]       

# Paleta fija (elige los que quieras)
PALETTE = [
    "#A3E635", "#F472B6", "#F59E0B", "#34D399",
    "#F87171", "#C084FC", "#FB7185", "#FBBF24"
]
def color_for_device(name: str) -> str:
    if not name:
        return "#9CA3AF"  # gris neutro fallback
    h = 0
    for ch in name:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return PALETTE[h % len(PALETTE)]


def _user_accessible_devices(request):
    if not getattr(request.user, "is_authenticated", False):
        return []
    return get_accessible_device_ids(
        username=getattr(request.user, "username", ""),
        email=getattr(request.user, "email", ""),
    )


def _user_default_device(request):
    if not getattr(request.user, "is_authenticated", False):
        return ""
    return get_default_device_id(
        username=getattr(request.user, "username", ""),
        email=getattr(request.user, "email", ""),
    )


def _get_region_map():
    """Return {name: color} from regiones collection."""
    try:
        return {
            str(r.get("name") or "").strip(): str(r.get("color") or "#6366F1").strip()
            for r in db["regiones"].find({})
            if str(r.get("name") or "").strip()
        }
    except Exception:
        return {}


def _ensure_region(name, default_color="#6366F1"):
    """Upsert a region entry if it does not already exist."""
    name = str(name or "").strip()
    if not name:
        return
    try:
        db["regiones"].update_one(
            {"name": name},
            {"$setOnInsert": {"name": name, "color": default_color}},
            upsert=True,
        )
    except Exception as e:
        print(f"[REGION] Could not ensure region '{name}': {e}")


def _can_delete_event(request, event_doc):
    if not getattr(request.user, "is_authenticated", False):
        return False
    if request.user.is_staff or request.user.is_superuser:
        return True
    created_by = str((event_doc or {}).get("created_by") or "").strip()
    return bool(created_by) and created_by == getattr(request.user, "username", "")


def _enqueue_events_reload(event_doc=None, target_devices=None):
    payload = {
        "type": "events_reload",
        "target": "events",
        "timestamp": timezone.now().isoformat(),
    }

    resolved_targets = []
    if target_devices is not None:
        for device_id in target_devices:
            value = str(device_id or "").strip()
            if value:
                resolved_targets.append(value)
    elif isinstance(event_doc, dict):
        for device_id in (event_doc.get("target_devices") or []):
            value = str(device_id or "").strip()
            if value:
                resolved_targets.append(value)
        if not resolved_targets:
            fallback_target = str(event_doc.get("target_device") or "").strip()
            if fallback_target:
                resolved_targets.append(fallback_target)

    if resolved_targets:
        seen = set()
        for device_id in resolved_targets:
            key = device_id.casefold()
            if key in seen:
                continue
            seen.add(key)
            enqueue_notification(device_id, dict(payload, to=device_id))
        return

    enqueue_broadcast_notification(dict(payload, to="all"))

class EventoList(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        eventos = Evento.objects.all()
        serializer = EventoSerializer(eventos, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = EventoSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(created_by=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
def home(request):
    """MONGO_URI = os.getenv('MONGO_URI')
    client = MongoClient(MONGO_URI)
    db = client['LabasAppDB']
    collection = db['eventos']"""

    regiones = db["eventos"].distinct("location")
    return render(request, "home.html", {
        "mensaje" : "Bienvenido a Labastida",
        "regiones": regiones
    })


"""def lista_eventos(request):
    MONGO_URI = os.getenv('MONGO_URI')
    client = MongoClient(MONGO_URI)
    db = client['LabasAppDB']
    collection = db['Eventos']

    filtro = {}
    location = request.GET.get('location')
    if location and location.lower() != 'all':
        filtro['location'] = location

    eventos = []
    for evento in collection.find(filtro):
        try:
            fecha_iso = datetime.strptime(evento['date'], "%d-%m-%Y").strftime("%Y-%m-%d")
        except ValueError:
            fecha_iso = None
        eventos.append({
            'title': evento.get('title', 'Sin título'),
            'date': fecha_iso,
            'description': evento.get('description', 'Sin descripción'),
            'location': evento.get('location', '')
        })

    # Obtener lista única de localizaciones
    regiones = collection.distinct('location')

    return render(request, 'eventos.html', {
        'eventos': eventos,
        'regiones': regiones
    })"""

def lista_eventos(request):
    collection = db["eventos"]
    condiciones = []
    is_admin = request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)
    mode = request.GET.get("mode")  # 'global' | 'personal' | 'admin' | None
    targets_param = (request.GET.get("targets") or "").strip()  # 'all' | 'a,b,c' | ''
    device_filter = request.GET.get("device", "all")

    # 1) Filtro por región
    location = request.GET.get("location")
    if location and location.lower() != "all":
        condiciones.append({"location": location})

    requested_admin_mode = mode == "admin" or (device_filter == "admin")
    if requested_admin_mode and not is_admin:
        mode = "global"
        requested_admin_mode = False

    linked_device = ""
    accessible_devices = []
    if request.user.is_authenticated:
        accessible_devices = _user_accessible_devices(request)
        linked_device = _user_default_device(request)

    # 2) Base visibility — skipped only in admin mode.
    if not requested_admin_mode:
        visibilidad = {"$or": [
            {"audience": {"$exists": False}},
            {"audience": "all"},
        ]}

        if request.user.is_authenticated:
            if accessible_devices:
                visibilidad["$or"].append({"audience": "device", "target_device": {"$in": accessible_devices}})
                visibilidad["$or"].append({"audience": "device", "target_devices": {"$in": accessible_devices}})
            visibilidad["$or"].append({"created_by": request.user.username})

        condiciones.append(visibilidad)

    # 3) Load all registered devices once; never call get_or_create_device here,
    # as that would silently create MongoDB docs for stale/orphan device IDs.
    _all_known = {
        str(d.get("device_id") or "").strip(): d
        for d in list_known_devices()
        if str(d.get("device_id") or "").strip()
    }

    if is_admin:
        selector_devices = [d for d in _all_known.values() if not d.get("hidden_in_admin")]
    else:
        selector_devices = [_all_known[did] for did in accessible_devices if did in _all_known]

    device_cards = []
    for device in selector_devices:
        did = str(device.get("device_id") or "").strip()
        if not did:
            continue
        device_cards.append({
            "device_id": did,
            "display_name": str(device.get("display_name") or did).strip() or did,
            "color": color_for_device(did),
            "status": device_online_status(device),
        })

    # The personal-mode filter picker shows exactly the same set as device_cards
    # (registered devices only, never orphan IDs from event history).
    my_devices = device_cards
    my_device_colors = my_devices

    # 5) Aplicar filtrado según modo
    if requested_admin_mode:
        mode = "admin"
        selected_targets = []
    elif mode == "global":
        condiciones.append({"$or": [
            {"audience": {"$exists": False}},
            {"audience": "all"},
        ]})

        selected_targets = []  # para la plantilla

    elif mode == "personal":
        condiciones.append({"audience": "device"})
        selected_targets = []
        if targets_param and targets_param != "all":
            _my_device_ids = {d["device_id"] for d in my_devices}
            selected_targets = [t for t in targets_param.split(",") if t in _my_device_ids]
            if selected_targets:
                condiciones.append({"$or": [
                    {"target_device": {"$in": selected_targets}},
                    {"target_devices": {"$in": selected_targets}},
                ]})

    else:
        # Modo legacy por ?device=
        selected_targets = []
        if device_filter == "global":
            condiciones.append({"$or": [
                {"audience": {"$exists": False}},
                {"audience": "all"},
            ]})
            mode = "global"
        elif device_filter == "admin" and is_admin:
            mode = "admin"
        elif device_filter not in ("all", "", None):
            condiciones.append({"audience": "device", "target_device": device_filter})
            mode = "personal"
            selected_targets = [device_filter]
        else:
            # Si no llega nada, por defecto vamos a 'global'
            mode = "global"
            condiciones.append({"$or": [
                {"audience": {"$exists": False}},
                {"audience": "all"},
            ]})

    filtro_final = {"$and": condiciones} if condiciones else {}
    region_map = _get_region_map()

    # 6) Construcción de eventos (igual que antes, con props extra para personales)
    eventos = []
    for evento in collection.find(filtro_final):
        try:
            fecha_iso = datetime.strptime(evento.get("date", ""), "%d-%m-%Y").strftime("%Y-%m-%d")
        except Exception:
            fecha_iso = None

        raw_tds = [str(d).strip() for d in (evento.get("target_devices") or []) if str(d).strip()]
        item = {
            "id": str(evento.get("_id") or ""),
            "title": evento.get("title", "Sin título"),
            "date": fecha_iso,
            "description": evento.get("description", "Sin descripción"),
            "location": evento.get("location", ""),
            "venue": evento.get("venue", ""),
            "all_day": bool(evento.get("all_day", True)),
            "start_time": evento.get("start_time", ""),
            "end_time": evento.get("end_time", ""),
            "target_devices": raw_tds,
            "created_by": str(evento.get("created_by") or "").strip(),
            "can_delete": _can_delete_event(request, evento),
        }
        if evento.get("audience") == "device":
            single = str(evento.get("target_device") or "").strip()
            multi  = [str(d).strip() for d in (evento.get("target_devices") or []) if str(d).strip()]
            all_targets = multi if multi else ([single] if single else [])
            names = []
            for did in all_targets:
                dev = _all_known.get(did)
                names.append(str((dev or {}).get("display_name") or did).strip() or did)
            item["color"] = color_for_device(all_targets[0] if all_targets else "")
            item["target_device"] = single
            item["target_devices_label"] = ", ".join(names) if names else ""
            item["audience"] = "device"
        else:
            item["audience"] = "all"

        region_color = region_map.get(str(evento.get("location") or "").strip(), "#6366F1")
        item["location_color"] = region_color

        eventos.append(item)

    # Build regiones list: from regiones collection + any unregistered event locations
    col_regions = list(db["regiones"].find({}).sort("name", 1))
    col_region_names = {str(r.get("name") or "").strip() for r in col_regions}
    regiones = [
        {"id": str(r["_id"]), "name": str(r.get("name") or "").strip(), "color": str(r.get("color") or "#6366F1").strip()}
        for r in col_regions if str(r.get("name") or "").strip()
    ]
    for loc in collection.distinct("location"):
        if loc and loc.strip() and loc.strip() not in col_region_names:
            regiones.append({"id": "", "name": loc.strip(), "color": "#6366F1"})

    return render(request, "eventos.html", {
        "eventos": eventos,
        "regiones": regiones,
        "linked_device": linked_device,
        "mode_selected": mode,
        "selected_targets": selected_targets,
        "my_devices": my_devices,
        "my_device_colors": my_device_colors,
        "device_cards": device_cards,
        "is_admin": is_admin,
    })


def app2 (request) :
    mensaje = _("tiempo")
    return render(request, 'app2.html', {'mensaje': mensaje})
    
@login_required                
@csrf_exempt
def guardar_evento(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)

            title       = data.get('title', 'Sin título')
            date_string = data.get('date')
            description = data.get('description', '')
            location    = data.get('location', '')
            venue       = data.get('venue', '')
            start_time  = (data.get('start_time') or '').strip()
            end_time    = (data.get('end_time') or '').strip()
            all_day     = bool(data.get('all_day', True))

            # Accept both single target_device and list target_devices
            raw_targets = data.get('target_devices') or []
            if isinstance(raw_targets, str):
                raw_targets = [raw_targets] if raw_targets.strip() else []
            target_devices = [str(t).strip() for t in raw_targets if str(t).strip()]

            # Determine audience from whether devices were selected
            audience = "device" if target_devices else (data.get('audience') or 'all').strip()

            # Normaliza la fecha a dd-mm-YYYY
            fecha_ddmm = None
            if date_string:
                fecha_ddmm = datetime.strptime(date_string, "%Y-%m-%d").strftime("%d-%m-%Y")

            if audience == 'device' and not target_devices:
                fallback = _user_default_device(request)
                if fallback:
                    target_devices = [fallback]
                else:
                    return JsonResponse({'success': False, 'error': 'Falta el mueble destino.'})

            if audience == 'device' and not (request.user.is_staff or request.user.is_superuser):
                allowed_devices = _user_accessible_devices(request)
                if allowed_devices:
                    forbidden = [d for d in target_devices if d not in allowed_devices]
                    if forbidden:
                        return JsonResponse({'success': False, 'error': 'No tienes acceso a alguno de los muebles destino.'}, status=403)

            doc = {
                "title"      : title,
                "date"       : fecha_ddmm,
                "description": description,
                "location"   : location,
                "venue"      : venue,
                "created_by" : request.user.username,
                "audience"   : "device" if audience == "device" else "all",
                "all_day"    : all_day,
                "start_time" : start_time if not all_day else "",
                "end_time"   : end_time if not all_day else "",
            }
            if audience == 'device':
                doc["target_devices"] = target_devices
                doc["target_device"] = target_devices[0]  # backward compat

            db["eventos"].insert_one(doc)
            _ensure_region(location)

            # ========== NOTIFICATION DEVICE QUEUE ==========
            try:
                if audience == 'device' and target_devices:
                    for td in target_devices:
                        payload = {
                            "type": "new_event",
                            "to": td,
                            "audience": "device",
                            "title": title,
                            "date": fecha_ddmm,
                            "description": description,
                            "location": location,
                            "venue": venue,
                            "all_day": all_day,
                            "start_time": start_time if not all_day else "",
                            "end_time": end_time if not all_day else "",
                            "timestamp": timezone.now().isoformat(),
                        }
                        enqueue_notification(td, payload)
                    print(f"[DEVICE QUEUE EVENTOS] ✓ Event notification enqueued to {target_devices}")
                elif audience == 'all':
                    payload = {
                        "type": "new_event",
                        "to": "all",
                        "audience": "all",
                        "title": title,
                        "date": fecha_ddmm,
                        "description": description,
                        "location": location,
                        "venue": venue,
                        "all_day": all_day,
                        "start_time": start_time if not all_day else "",
                        "end_time": end_time if not all_day else "",
                        "timestamp": timezone.now().isoformat(),
                    }
                    inserted = enqueue_broadcast_notification(payload)
                    print(f"[DEVICE QUEUE EVENTOS] ✓ Broadcast event enqueued to {len(inserted)} device(s)")
            except Exception as e:
                print(f"[DEVICE QUEUE EVENTOS] ✗ Queue error: {e}")
                import traceback
                traceback.print_exc()
            # =============================================

            return JsonResponse({'success': True})

        except Exception as e:
            print("[guardar_evento] Error:", repr(e))
            return JsonResponse({'success': False, 'error': repr(e)})

    return JsonResponse({'success': False, 'error': 'Método no permitido'})


@login_required
@csrf_exempt
def actualizar_evento(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método no permitido'}, status=405)

    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON inválido'}, status=400)

    event_id = str(data.get('event_id') or '').strip()
    if not event_id:
        return JsonResponse({'success': False, 'error': 'Falta el id del evento'}, status=400)

    try:
        mongo_id = ObjectId(event_id)
    except Exception:
        return JsonResponse({'success': False, 'error': 'Id de evento inválido'}, status=400)

    collection = db['eventos']
    event_doc = collection.find_one({'_id': mongo_id})
    if not event_doc:
        return JsonResponse({'success': False, 'error': 'Evento no encontrado'}, status=404)

    if not _can_delete_event(request, event_doc):
        return JsonResponse({'success': False, 'error': 'No tienes permisos para editar este evento'}, status=403)

    try:
        title       = data.get('title', 'Sin título')
        date_string = data.get('date')
        description = data.get('description', '')
        location    = data.get('location', '')
        venue       = data.get('venue', '')
        start_time  = (data.get('start_time') or '').strip()
        end_time    = (data.get('end_time') or '').strip()
        all_day     = bool(data.get('all_day', True))

        raw_targets = data.get('target_devices') or []
        if isinstance(raw_targets, str):
            raw_targets = [raw_targets] if raw_targets.strip() else []
        target_devices = [str(t).strip() for t in raw_targets if str(t).strip()]
        audience = "device" if target_devices else "all"

        fecha_ddmm = None
        if date_string:
            fecha_ddmm = datetime.strptime(date_string, "%Y-%m-%d").strftime("%d-%m-%Y")

        update_fields = {
            "title"      : title,
            "date"       : fecha_ddmm,
            "description": description,
            "location"   : location,
            "venue"      : venue,
            "audience"   : audience,
            "all_day"    : all_day,
            "start_time" : start_time if not all_day else "",
            "end_time"   : end_time if not all_day else "",
            "target_devices": target_devices if audience == "device" else [],
            "target_device" : target_devices[0] if audience == "device" and target_devices else "",
        }

        collection.update_one({'_id': mongo_id}, {'$set': update_fields})
        _ensure_region(location)

        try:
            old_targets = [str(d).strip() for d in (event_doc.get("target_devices") or []) if str(d).strip()]
            if event_doc.get("target_device") and event_doc["target_device"] not in old_targets:
                old_targets.append(str(event_doc["target_device"]).strip())
            all_notify = list({d for d in old_targets + target_devices if d})
            if all_notify:
                _enqueue_events_reload(target_devices=all_notify)
        except Exception as exc:
            print(f"[DEVICE QUEUE EVENTOS] ✗ Reload enqueue error after update: {exc}")

        return JsonResponse({'success': True})

    except Exception as e:
        print("[actualizar_evento] Error:", repr(e))
        return JsonResponse({'success': False, 'error': repr(e)})


@login_required
@csrf_exempt
def delete_evento(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método no permitido'}, status=405)

    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON body.'}, status=400)

    event_id = str(data.get('event_id') or '').strip()
    if not event_id:
        return JsonResponse({'success': False, 'error': 'Missing event id.'}, status=400)

    try:
        mongo_id = ObjectId(event_id)
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid event id.'}, status=400)

    collection = db['eventos']
    event_doc = collection.find_one({'_id': mongo_id})
    if not event_doc:
        return JsonResponse({'success': False, 'error': 'Event not found.'}, status=404)

    if not _can_delete_event(request, event_doc):
        return JsonResponse({'success': False, 'error': 'No tienes permisos para borrar este evento.'}, status=403)

    result = collection.delete_one({'_id': mongo_id})
    if result.deleted_count != 1:
        return JsonResponse({'success': False, 'error': 'Could not delete event.'}, status=500)

    try:
        _enqueue_events_reload(event_doc=event_doc)
    except Exception as exc:
        print(f"[DEVICE QUEUE EVENTOS] ✗ Reload enqueue error after delete: {exc}")

    return JsonResponse({'success': True})


# ── Region management views ──────────────────────────────────────────────────

@login_required
def list_regiones(request):
    try:
        regions = [
            {"id": str(r["_id"]), "name": str(r.get("name") or ""), "color": str(r.get("color") or "#6366F1")}
            for r in db["regiones"].find({}).sort("name", 1)
        ]
        return JsonResponse({"success": True, "regions": regions})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@login_required
@csrf_exempt
def create_region(request):
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({"success": False, "error": "No autorizado"}, status=403)
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Método no permitido"}, status=405)
    try:
        data = json.loads(request.body or '{}')
        name = str(data.get("name") or "").strip()
        color = str(data.get("color") or "#6366F1").strip()
        if not name:
            return JsonResponse({"success": False, "error": "El nombre es obligatorio"}, status=400)
        if db["regiones"].find_one({"name": name}):
            return JsonResponse({"success": False, "error": "Ya existe una región con ese nombre"}, status=400)
        result = db["regiones"].insert_one({"name": name, "color": color})
        return JsonResponse({"success": True, "id": str(result.inserted_id), "name": name, "color": color})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@login_required
@csrf_exempt
def update_region(request, region_id):
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({"success": False, "error": "No autorizado"}, status=403)
    if request.method not in ("POST", "PUT"):
        return JsonResponse({"success": False, "error": "Método no permitido"}, status=405)
    try:
        data = json.loads(request.body or '{}')
        region_id_obj = ObjectId(region_id)
    except Exception:
        return JsonResponse({"success": False, "error": "ID inválido"}, status=400)
    try:
        old_doc = db["regiones"].find_one({"_id": region_id_obj})
        if not old_doc:
            return JsonResponse({"success": False, "error": "Región no encontrada"}, status=404)
        update_fields = {}
        new_name = str(data.get("name") or "").strip()
        new_color = str(data.get("color") or "").strip()
        if new_name and new_name != str(old_doc.get("name") or "").strip():
            if db["regiones"].find_one({"name": new_name, "_id": {"$ne": region_id_obj}}):
                return JsonResponse({"success": False, "error": "Ya existe una región con ese nombre"}, status=400)
            old_name = str(old_doc.get("name") or "").strip()
            if old_name:
                db["eventos"].update_many({"location": old_name}, {"$set": {"location": new_name}})
            update_fields["name"] = new_name
        if new_color:
            update_fields["color"] = new_color
        if update_fields:
            db["regiones"].update_one({"_id": region_id_obj}, {"$set": update_fields})
        return JsonResponse({"success": True})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@login_required
@csrf_exempt
def delete_region(request, region_id):
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({"success": False, "error": "No autorizado"}, status=403)
    if request.method not in ("POST", "DELETE"):
        return JsonResponse({"success": False, "error": "Método no permitido"}, status=405)
    try:
        region_id_obj = ObjectId(region_id)
    except Exception:
        return JsonResponse({"success": False, "error": "ID inválido"}, status=400)
    try:
        region_doc = db["regiones"].find_one({"_id": region_id_obj})
        if not region_doc:
            return JsonResponse({"success": False, "error": "Región no encontrada"}, status=404)
        region_name = str(region_doc.get("name") or "").strip()
        events_count = db["eventos"].count_documents({"location": region_name}) if region_name else 0
        if events_count > 0:
            return JsonResponse({
                "success": False,
                "error": f"No se puede eliminar: {events_count} evento(s) usan esta región. Reasígnalos primero.",
                "events_count": events_count,
            }, status=400)
        db["regiones"].delete_one({"_id": region_id_obj})
        return JsonResponse({"success": True})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)


def generate_video_token(request, identity, room_name):
    try:
        # Configura tus credenciales de Twilio
        twilio_account_sid = settings.TWILIO_ACCOUNT_SID  # <--  ACCOUNT SID
        twilio_api_key = settings.TWILIO_API_KEY  # <-- API KEY
        twilio_api_secret = settings.TWILIO_API_SECRET  # <-- API SECRET

        token = AccessToken(
            twilio_account_sid,  
            twilio_api_key,
            twilio_api_secret,
            identity=identity,
        )

        token.ttl = 600  

        video_grant = VideoGrant(room=room_name)
        token.add_grant(video_grant)

        enqueue_videocall_notification(room_name, identity)

        return JsonResponse({
            'token': str(token.to_jwt()),
            'room_name': room_name
        })
    except Exception:
        return JsonResponse({'error': 'No se pudo generar el token'}, status=500)

@csrf_exempt
def toggle_emotion_detection(request):
    CONTROL_FILE = "C:/Users/Jaime/Mast-TFM/Emociones/status_emotion.txt"

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            status = data.get('status')  # "enabled" o "disabled"
            identity = data.get('identity')
            room = data.get('room')

            print(f"[EMOCIÓN] {identity} en sala {room} cambió estado a: {status}")

            with open(CONTROL_FILE, 'w') as f:
                f.write(status)

            return JsonResponse({'success': True, 'status': status})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})

    return JsonResponse({'success': False, 'error': 'Método no permitido'})

def login_required_message(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            # 1. Mensaje que verás en el login
            messages.warning(request,
                             "Debes iniciar sesión para acceder a Videollamadas.")
            # 2. Construimos login?next=/videocall/
            login_url = f"{reverse('login')}?next={request.path}"
            return redirect(login_url)
        return view_func(request, *args, **kwargs)
    return _wrapped


@login_required_message
def videocall(request):
    is_admin = getattr(request.user, "is_staff", False) or getattr(request.user, "is_superuser", False)
    if is_admin:
        all_devs = list_known_devices()
        room_options = [
            {"id": d["device_id"], "label": d.get("display_name") or d["device_id"]}
            for d in all_devs
            if d.get("enabled", True) and not d.get("hidden_in_admin", False)
        ]
    else:
        accessible = _user_accessible_devices(request)
        room_options = [{"id": r, "label": r} for r in accessible]

    prefill = (request.GET.get("to") or _user_default_device(request)).strip()
    option_ids = [o["id"] for o in room_options]
    if prefill and option_ids and prefill not in option_ids:
        prefill = option_ids[0] if option_ids else ""

    return render(request, "videocall.html", {
        "identity": request.user.username,
        "default_room": prefill,
        "available_rooms": [o["id"] for o in room_options],
        "room_options": room_options,
    })


def videocall_device(request):
    return render(
        request,
        "videocall.html",
        {
            "identity": "",
            "default_room": "",
            "available_rooms": [],
            "device_mode": True,
        },
    )


@csrf_exempt
def device_videocall_session(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        payload = {}

    device_id = str(
        request.headers.get("X-DEVICE-ID")
        or payload.get("device_id")
        or ""
    ).strip()
    device_key = str(
        request.headers.get("X-DEVICE-KEY")
        or payload.get("device_key")
        or ""
    ).strip()
    requested_room = str(payload.get("room") or "").strip()

    if not device_id:
        return JsonResponse({"error": "Missing device_id"}, status=400)
    if not verify_device_videocall_key(device_id, device_key):
        return JsonResponse({"error": "Unauthorized"}, status=401)

    ctx = get_device_videocall_context(device_id)
    if not ctx:
        return JsonResponse({"error": "Unknown or disabled device"}, status=404)

    allowed_room = ctx["allowed_room"]
    room_name = requested_room or allowed_room
    if room_name != allowed_room:
        return JsonResponse({"error": "Room not allowed for this device"}, status=403)

    token = AccessToken(
        settings.TWILIO_ACCOUNT_SID,
        settings.TWILIO_API_KEY,
        settings.TWILIO_API_SECRET,
        identity=device_id,
    )
    token.ttl = 600
    token.add_grant(VideoGrant(room=room_name))

    return JsonResponse(
        {
            "token": str(token.to_jwt()),
            "room_name": room_name,
            "identity": device_id,
            "device_name": ctx["display_name"],
            "call_answered_url": request.build_absolute_uri(reverse("call_answered")),
        }
    )

def enqueue_videocall_notification(room_name: str, caller: str) -> None:
    """
    Enqueue unified videocall notification for one furniture device.
    
    NOUVEAU FORMAT:
    {
        "type": "videocall",
        "from": "Ana",
        "to": "maria",
        "room": "maria",
        "timestamp": "2024-12-11T16:00:00"
    }
    """
    try:
        target_device_id = resolve_device_id_for_queue_target(room_name)
        payload = {
            "type": "videocall",
            "from": caller,
            "to": target_device_id or room_name,
            "room": room_name,
            "timestamp": timezone.now().isoformat()
        }
        enqueue_notification(target_device_id or room_name, payload)
        call_monitor.add_call(room_name=room_name, caller=caller)
    except Exception:
        pass

@csrf_exempt
def call_answered(request):
    """Endpoint appelé par le frontend quand le meuble décroche"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    try:
        data = json.loads(request.body)
        room_name = data.get('room')
        device = data.get('device')
        
        if not room_name:
            return JsonResponse({'error': 'Missing room'}, status=400)
        
        call_monitor.mark_answered(room_name)
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# Démarrer le call monitor au chargement du module si está habilitado
call_monitor.start()


