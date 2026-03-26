import os
from twilio.rest import Client

# Configura tus credenciales de Twilio
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "AC028c91441b201dcb8b2ea31ac92ea82a")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "5b4fc9557b92cfa3f2a0ddfdc9d120e8")

def main():
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    # Crear un nuevo servicio de conversación
    servicio = client.conversations.v1.services.create(
        friendly_name=os.getenv("TWILIO_SERVICE_FRIENDLY_NAME", "asociacion_chat")
    )

    print(f"Nuevo servicio creado con SID: {servicio.sid}")


if __name__ == "__main__":
    main()
