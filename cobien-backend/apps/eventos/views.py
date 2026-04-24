from django.shortcuts import redirect, render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import Evento
from .serializers import EventoSerializer
from django.utils.translation import gettext as _
from datetime import datetime
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
import openai
from django.core.files.storage import default_storage
import os
import base64
from django.conf import settings
import re
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VideoGrant
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from functools import wraps
from pymongo import MongoClient
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



openai.api_key = os.getenv(
    "OPENAI_API_KEY",
    "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",
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

    # 1) Filtro por región
    location = request.GET.get("location")
    if location and location.lower() != "all":
        condiciones.append({"location": location})

    # 2) Visibilidad base — events for all + device events the user can see
    visibilidad = {"$or": [
        {"audience": {"$exists": False}},
        {"audience": "all"},
    ]}

    linked_device = ""
    accessible_devices = []
    if request.user.is_authenticated:
        accessible_devices = _user_accessible_devices(request)
        linked_device = _user_default_device(request)

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

    # 4) NUEVO: parámetros modernos
    mode = request.GET.get("mode")  # 'global' | 'personal' | None
    targets_param = (request.GET.get("targets") or "").strip()  # 'all' | 'a,b,c' | ''

    # Compat anterior
    device_filter = request.GET.get("device", "all")

    # 5) Aplicar filtrado según modo
    if mode == "global":
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

    # 6) Construcción de eventos (igual que antes, con props extra para personales)
    eventos = []
    for evento in collection.find(filtro_final):
        try:
            fecha_iso = datetime.strptime(evento.get("date", ""), "%d-%m-%Y").strftime("%Y-%m-%d")
        except Exception:
            fecha_iso = None

        item = {
            "title": evento.get("title", "Sin título"),
            "date": fecha_iso,
            "description": evento.get("description", "Sin descripción"),
            "location": evento.get("location", "")
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
            item["created_by"] = str(evento.get("created_by") or "").strip()

        eventos.append(item)

    regiones = collection.distinct("location")

    return render(request, "eventos.html", {
        "eventos": eventos,
        "regiones": regiones,
        "linked_device": linked_device,
        "mode_selected": mode,
        "selected_targets": selected_targets,
        "my_devices": my_devices,
        "my_device_colors": my_device_colors,
        "device_cards": device_cards,
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
                "created_by" : request.user.username,
                "audience"   : "device" if audience == "device" else "all",
            }
            if audience == 'device':
                doc["target_devices"] = target_devices
                doc["target_device"] = target_devices[0]  # backward compat

            db["eventos"].insert_one(doc)

            # ========== NOTIFICATION DEVICE QUEUE ==========
            try:
                if audience == 'device' and target_devices:
                    for td in target_devices:
                        payload = {
                            "type": "new_event",
                            "to": td,
                            "title": title,
                            "date": fecha_ddmm,
                            "description": description,
                            "location": location,
                            "timestamp": datetime.now().isoformat(),
                        }
                        enqueue_notification(td, payload)
                    print(f"[DEVICE QUEUE EVENTOS] ✓ Event notification enqueued to {target_devices}")
                elif audience == 'all':
                    payload = {
                        "type": "new_event",
                        "to": "all",
                        "title": title,
                        "date": fecha_ddmm,
                        "description": description,
                        "location": location,
                        "timestamp": datetime.now().isoformat(),
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

@csrf_exempt
def extraer_evento(request):
    """
    Guarda la imagen en appEventos/media/uploads, la envía a GPT-4o,
    y devuelve título, fecha, lugar y descripción.
    """
    if request.method == 'POST' and request.FILES.get('image'):
        try:
            # Guardar la imagen temporalmente
            image_file = request.FILES['image']
            temp_path = default_storage.save(f"uploads/{image_file.name}", image_file)
            image_path = os.path.join(settings.MEDIA_ROOT, temp_path)

            # Codificar la imagen en Base64
            with open(image_path, "rb") as img_file:
                base64_image = base64.b64encode(img_file.read()).decode('utf-8')

            # Enviar la imagen a GPT-4o
            response = openai.ChatCompletion.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Extrae el título, fecha, lugar y una breve descripción de la imagen."},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}}
                    ]}
                ],
                max_tokens=500
            )

            # Procesar la respuesta
            response_content = response['choices'][0]['message']['content']
            event_data = parse_response(response_content)

            # Eliminar la imagen temporal
            default_storage.delete(temp_path)

            return JsonResponse({'success': True, **event_data})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})

    return JsonResponse({'success': False, 'error': 'No se ha subido ninguna imagen.'})


def parse_response(response_text):
    """
    Parsea la respuesta de OpenAI y extrae título, fecha, lugar y descripción.
    Convierte la fecha al formato yyyy-mm-dd.
    """
    meses = {
        "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
        "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
        "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12"
    }

    lines = response_text.split("\n")
    event_data = {'title': '', 'date': '', 'place': '', 'description': ''}

    for line in lines:
        if "Título:" in line:
            event_data['title'] = line.split(":", 1)[1].strip()
        elif "Fecha:" in line:
            raw_date = line.split(":", 1)[1].strip()
            print("Fecha recibida de OpenAI:", raw_date)

            try:
                # Limpieza básica
                # Limpieza básica
                clean_date = re.sub(r'[^\w\sáéíóúÁÉÍÓÚ]', '', raw_date)  # Elimina caracteres especiales como *, , etc.
                clean_date = re.sub(r'^\s*(lunes|martes|miércoles|jueves|viernes|sábado|domingo),?\s*', '', clean_date, flags=re.IGNORECASE)
                clean_date = re.sub(r',.*$', '', clean_date)  # Elimina todo lo que viene después de la coma
                print("Fecha limpia:", clean_date)

                # Manejo de formato día de mes (2 de marzo, 2024)
                match = re.match(r'(\d+)\sde\s([a-zA-ZáéíóúÁÉÍÓÚ]+)(?:\sde\s(\d{4}))?', clean_date)
                if match:
                    day = match.group(1).zfill(2)
                    month_name = match.group(2).lower()
                    month = meses.get(month_name, "01")
                    year = match.group(3) if match.group(3) else str(datetime.now().year)

                    # Formato yyyy-mm-dd
                    formatted_date = f"{year}-{month}-{day}"
                    print("Fecha formateada (HTML):", formatted_date)
                    event_data['date'] = formatted_date
                else:
                    print("No se pudo parsear la fecha:", raw_date)
                    event_data['date'] = ''  # Fecha inválida
            except Exception as e:
                print("Error al convertir la fecha:", e)
                event_data['date'] = ''  # Fecha inválida

        elif "Lugar:" in line:
            event_data['place'] = line.split(":", 1)[1].strip()
        elif "Descripción:" in line:
            event_data['description'] = line.split(":", 1)[1].strip()

    return event_data

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

        print(f"Token generado JWT: {token.to_jwt()}")  
        enqueue_videocall_notification(room_name, identity) 

        # Devolver el token como JSON
        return JsonResponse({
            'token': str(token.to_jwt()),
            'room_name': room_name
        })
    except Exception as e:
        print(f"Error al generar el token: {e}")
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
            "timestamp": datetime.now().isoformat()
        }
        enqueue_notification(target_device_id or room_name, payload)
        print(f"[DEVICE QUEUE VIDEOCALL] ✓ Notification enqueued")
        print(f"[DEVICE QUEUE VIDEOCALL]   From: {caller}")
        print(f"[DEVICE QUEUE VIDEOCALL]   To room: {room_name}")
        print(f"[DEVICE QUEUE VIDEOCALL]   Target device: {target_device_id or room_name}")
        print(f"[DEVICE QUEUE VIDEOCALL]   Type: videocall")
        print(f"[DEVICE QUEUE VIDEOCALL]   Payload: {json.dumps(payload)}")

        call_monitor.add_call(room_name=room_name, caller=caller)
    
    except Exception as e:
        print(f"[DEVICE QUEUE VIDEOCALL] ✗ Erreur queue: {e}")
        import traceback
        traceback.print_exc()

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
        
        print(f"[CALL ANSWERED] 📞 Appel décroché")
        print(f"[CALL ANSWERED]    Room: {room_name}")
        print(f"[CALL ANSWERED]    Device: {device}")
        
        call_monitor.mark_answered(room_name)
        
        return JsonResponse({'success': True})
    
    except Exception as e:
        print(f"[CALL ANSWERED] ❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=500)


# Démarrer le call monitor au chargement du module si está habilitado
call_monitor.start()


""" ================= Solution espanol =================="""
#def enqueue_videocall_notification(room_name: str, caller: str) -> None:
#    """Lanza la notificación al broker en los 3 topics necesarios."""
#
#    # 1) Mensaje detallado (por sala) – mantiene compatibilidad con diseño inicial
#    topic_room   = f"calls/{room_name}"
#    payload_room = json.dumps({
#        "action": "incoming_call",
#        "room"  : room_name,
#        "from"  : caller,
#    })
#
#    # 2) Mensaje que espera la app Kivy para abrir la pantalla de videollamada
#    topic_video   = settings.MQTT_TOPIC_VIDEOCALL         # «videollamada» por defecto
#    payload_video = f"videollamada:{caller}"              # ej. «videollamada:Ana»
#
#    # 3) Topic genérico para otra lógica (menú por voz, etc.)
#    topic_general   = settings.MQTT_TOPIC_GENERAL         # «tarjeta» por defecto
#    payload_general = "videollamada"                      # mando la keyword sola
#
#    messages = [
#        {"topic": topic_room,    "payload": payload_room,    "qos": 1},
#        {"topic": topic_video,   "payload": payload_video,   "qos": 1},
#        {"topic": topic_general, "payload": payload_general, "qos": 1},
#    ]
#
#    auth = None
#    if settings.MQTT_USERNAME:
#        auth = {"username": settings.MQTT_USERNAME, "password": settings.MQTT_PASSWORD}
#
#    mqtt_publish.multiple(
#        msgs     = messages,
#        hostname = settings.MQTT_BROKER_URL,
#        port     = settings.MQTT_BROKER_PORT,
#        auth     = auth,
#    )
#
#    print("[MQTT] → enviado a:")
#    for m in messages:
#        print("   ", m["topic"], m["payload"])
"""=========== Solution espanol =============="""
