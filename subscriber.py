

# --------------------------------------------------------------------
# --------------------------------------------------------------------
# --------------------------------------------------------------------
# THIS IS A TEST FILE AND SHOULD BE DELETED
# --------------------------------------------------------------------
# --------------------------------------------------------------------
# --------------------------------------------------------------------
# subscriber.py


import sys
import paho.mqtt.client as mqtt

BROKER = "broker.hivemq.com"
PORT   = 1883               # 8884 con TLS

# Topics por defecto si no se pasan argumentos
DEFAULT_TOPICS = ["tarjeta", "videollamada"]

# --------------------------------------------------------------------
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(" Conectado a HiveMQ")
        # Suscribirse a todos los topics solicitados
        for t in userdata["topics"]:
            client.subscribe(t)
            print(f" Suscrito a '{t}'")
        print("Esperando mensajes…\n")
    else:
        print(f" Error de conexión (rc={rc})")

def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode(errors="replace")
    except Exception:
        payload = str(msg.payload)
    print(f" [{msg.topic}] {payload}")

# --------------------------------------------------------------------
if __name__ == "__main__":
    topics = sys.argv[1:] or DEFAULT_TOPICS

    client = mqtt.Client(client_id="demo_sub", userdata={"topics": topics})
    client.on_connect  = on_connect
    client.on_message  = on_message

    # Para TLS descomenta la línea siguiente y cambia PORT = 8884
    # client.tls_set()

    client.connect(BROKER, PORT, keepalive=60)
    client.loop_forever()
