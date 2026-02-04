import json
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import ChatGrant
from django.conf import settings
from twilio.rest import Client

client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

# Generar un Access Token para un usuario específico
@csrf_exempt
def generate_twilio_token(request):
    """Genera un token de acceso para Twilio Conversations"""
    identity = request.POST.get("identity")
    
    if not identity:
        return JsonResponse({"error": "No se proporcionó una identidad"}, status=400)

    # Crear el token
    token = AccessToken(
        settings.TWILIO_ACCOUNT_SID,
        settings.TWILIO_API_KEY,
        settings.TWILIO_API_SECRET,
        identity=identity
    )

    # Conceder acceso a Twilio Conversations
    chat_grant = ChatGrant(service_sid=settings.TWILIO_CONVERSATION_SERVICE_SID)
    token.add_grant(chat_grant)

    # Agregar al usuario a la conversación si no está ya dentro
    conversation = client.conversations.v1.conversations(CONVERSATION_SID)

    participants = conversation.participants.list()
    existing_participants = [p.identity for p in participants]

    if identity not in existing_participants:
        conversation.participants.create(identity=identity)
        print(f"{identity} agregado a la conversación.")

    return JsonResponse({"token": token.to_jwt()})

def chat_asociacion(request):
    return render(request, "asociacion.html")  


def obtener_o_crear_conversacion():
    """Verifica si existe la conversación global y la crea si no existe"""
    conversations = client.conversations.v1.conversations.list()

    # Buscar si ya existe la conversación
    for conversation in conversations:
        if conversation.friendly_name == "asociacion_chat":
            print(f"Usando conversación existente: {conversation.sid}")
            return conversation.sid  

    # Si no existe, crear una nueva
    conversation = client.conversations.v1.conversations.create(
        friendly_name="asociacion_chat"
    )
    print(f"Nueva conversación creada: {conversation.sid}")
    return conversation.sid

CONVERSATION_SID = obtener_o_crear_conversacion()