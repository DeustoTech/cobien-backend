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
import paho.mqtt.publish as mqtt_publish
from rest_framework.permissions import IsAuthenticated
from .call_monitor import call_monitor



openai.api_key = "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"
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

    # 1) Filtro por región (igual que antes)
    location = request.GET.get("location")
    if location and location.lower() != "all":
        condiciones.append({"location": location})

    # 2) Visibilidad base (igual que antes)
    visibilidad = {"$or": [
        {"audience": {"$exists": False}},
        {"audience": "all"},
    ]}

    linked_device = ""
    if request.user.is_authenticated:
        user_doc = db["auth_user"].find_one(
            {"username": request.user.username},
            {"target_device": 1, "default_room": 1}
        )
        linked_device = (user_doc.get("target_device") or user_doc.get("default_room") or "") if user_doc else ""

        if linked_device:
            visibilidad["$or"].append({"audience": "device", "target_device": linked_device})
        visibilidad["$or"].append({"created_by": request.user.username})

    condiciones.append(visibilidad)

    # 3) Opciones visibles para el picker (igual base que antes)
    filtro_visible = {"$and": (condiciones[:])} if condiciones else {}
    filtro_devices = {"$and": (condiciones[:] + [{"audience": "device"}])} if condiciones else {"audience": "device"}
    my_devices = sorted(d for d in collection.distinct("target_device", filtro_devices) if d)
    my_device_colors = [{"name": d, "color": color_for_device(d)} for d in my_devices]

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
        # Normalizamos la lista de seleccionados; sólo aceptamos los que realmente son visibles
        selected_targets = []
        if targets_param and targets_param != "all":
            selected_targets = [t for t in targets_param.split(",") if t in my_devices]
            if selected_targets:
                condiciones.append({"target_device": {"$in": selected_targets}})
        # Si 'all' o vacío, mostramos todos los personales visibles (no añadimos condición extra)

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
            item["color"] = color_for_device(evento.get("target_device", ""))
            item["target_device"] = evento.get("target_device", "")
            item["created_by"] = evento.get("created_by", "")

        eventos.append(item)

    regiones = collection.distinct("location")

    return render(request, "eventos.html", {
        "eventos": eventos,
        "regiones": regiones,
        "linked_device": linked_device,
        # Para la plantilla nueva:
        "mode_selected": mode,
        "selected_targets": selected_targets,
        "my_devices": my_devices,
        "my_device_colors": my_device_colors,
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

            audience      = (data.get('audience') or 'all').strip()       # "all" | "device"
            target_device = (data.get('target_device') or '').strip()

            # Normaliza la fecha a dd-mm-YYYY (como en tus documentos)
            fecha_ddmm = None
            if date_string:
                fecha_ddmm = datetime.strptime(date_string, "%Y-%m-%d").strftime("%d-%m-%Y")

            # Si es para mueble y no se pasó, usa el vinculado del usuario
            if audience == 'device' and not target_device:
                user_doc = db["auth_user"].find_one(
                    {"username": request.user.username},
                    {"target_device": 1, "default_room": 1}
                )
                if user_doc:
                    target_device = user_doc.get("target_device") or user_doc.get("default_room") or ""
                if not target_device:
                    return JsonResponse({'success': False, 'error': 'Falta el usuario de mueble destino.'})

            doc = {
                "title"      : title,
                "date"       : fecha_ddmm,
                "description": description,
                "location"   : location,
                "created_by" : request.user.username,
                "audience"   : "device" if audience == "device" else "all"
            }
            if audience == 'device':
                doc["target_device"] = target_device

            db["eventos"].insert_one(doc)
            
            # ========== NOTIFICATION MQTT UNIFIÉE ==========
            try:
                if audience == 'device' and target_device:
                    # 📍 ÉVÉNEMENT PERSONNEL
                    payload = json.dumps({
                        "type": "new_event",
                        "to": target_device,
                        "title": title,
                        "date": fecha_ddmm,
                        "description": description,
                        "location": location,
                        "timestamp": datetime.now().isoformat()
                    })
                    
                    mqtt_publish.single(
                        topic=settings.MQTT_TOPIC_GENERAL,
                        payload=payload,
                        hostname=settings.MQTT_BROKER_URL,
                        port=settings.MQTT_BROKER_PORT,
                        qos=1
                    )
                    
                    print(f"[MQTT EVENTOS] ✓ Notification personnel envoyée")
                    print(f"[MQTT EVENTOS]   To: {target_device}")
                    print(f"[MQTT EVENTOS]   Type: new_event")
                    print(f"[MQTT EVENTOS]   Topic: {settings.MQTT_TOPIC_GENERAL}")
                    print(f"[MQTT EVENTOS]   Payload: {payload}")
                
                elif audience == 'all':
                    # 📢 ÉVÉNEMENT PUBLIC
                    payload = json.dumps({
                        "type": "new_event",
                        "to": "all",
                        "title": title,
                        "date": fecha_ddmm,
                        "description": description,
                        "location": location,
                        "timestamp": datetime.now().isoformat()
                    })
                    
                    mqtt_publish.single(
                        topic=settings.MQTT_TOPIC_GENERAL,
                        payload=payload,
                        hostname=settings.MQTT_BROKER_URL,
                        port=settings.MQTT_BROKER_PORT,
                        qos=1
                    )
                    
                    print(f"[MQTT EVENTOS] ✓ Notification publique envoyée")
                    print(f"[MQTT EVENTOS]   To: all")
                    print(f"[MQTT EVENTOS]   Type: new_event")
                    print(f"[MQTT EVENTOS]   Topic: {settings.MQTT_TOPIC_GENERAL}")
                    print(f"[MQTT EVENTOS]   Payload: {payload}")

            except Exception as e:
                print(f"[MQTT EVENTOS] ✗ Erreur MQTT: {e}")
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

        user_doc = db["auth_user"].find_one(
            {"username": identity},
            {"default_room": 1}
        )
        if not user_doc or not user_doc.get("default_room"):
            db["auth_user"].update_one(
                {"username": identity},
                {"$set": {"default_room": room_name}}
            )

        print(f"Token generado JWT: {token.to_jwt()}")  
        send_mqtt_notification(room_name, identity) 

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
    user_doc = db["auth_user"].find_one(
        {"username": request.user.username},
        {"default_room": 1}
    )
    prefill = (request.GET.get("to") or user_doc.get("default_room", "")).strip()

    return render(request, "videocall.html", {
        "identity": request.user.username,
        "default_room": prefill,
    })

def send_mqtt_notification(room_name: str, caller: str) -> None:
    """
    Envoie notification videocall unifiée sur topic 'tarjeta'
    
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
        # ✅ NOTIFICATION UNIFIÉE
        payload = json.dumps({
            "type": "videocall",
            "from": caller,
            "to": room_name,  # Le destinataire = la room
            "room": room_name,
            "timestamp": datetime.now().isoformat()
        })
        
        auth = None
        if settings.MQTT_USERNAME:
            auth = {
                "username": settings.MQTT_USERNAME,
                "password": settings.MQTT_PASSWORD
            }
        
        mqtt_publish.single(
            topic=settings.MQTT_TOPIC_GENERAL,  # "tarjeta"
            payload=payload,
            hostname=settings.MQTT_BROKER_URL,
            port=settings.MQTT_BROKER_PORT,
            auth=auth,
            qos=1
        )
        
        print(f"[MQTT VIDEOCALL] ✓ Notification envoyée")
        print(f"[MQTT VIDEOCALL]   From: {caller}")
        print(f"[MQTT VIDEOCALL]   To: {room_name}")
        print(f"[MQTT VIDEOCALL]   Type: videocall")
        print(f"[MQTT VIDEOCALL]   Topic: {settings.MQTT_TOPIC_GENERAL}")
        print(f"[MQTT VIDEOCALL]   Payload: {payload}")

        call_monitor.add_call(room_name=room_name, caller=caller)
    
    except Exception as e:
        print(f"[MQTT VIDEOCALL] ✗ Erreur MQTT: {e}")
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


# Démarrer le call monitor au chargement du module
call_monitor.start()
print("[VIEWS] ✅ Call monitor démarré")


""" ================= Solution espanol =================="""
#def send_mqtt_notification(room_name: str, caller: str) -> None:
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
