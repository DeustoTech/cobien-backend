# App `eventos` — READMEs

> Esta app agrupa **dos funcionalidades** principales y parte del frontend:
>
> 1. **Eventos** (calendario/listado, alta y filtrado) — persiste en **MongoDB** y expone una API DRF para el modelo `Evento` (SQL).
> 2. **Videollamada** — integra **Twilio Video** y publica **notificaciones MQTT**.
>    Además contiene la mayoría de **templates HTML** y **CSS** de estas pantallas.

---

## Índice

* [README — Eventos](#readme--eventos)
* [README — Videollamada](#readme--videollamada)
* [Guía — Templates & CSS](#guía--templates--css)
* [Checklist de configuración](#checklist-de-configuración)
* [Quickstart local](#quickstart-local)

---

## README — Eventos

### 1) Qué hace

* Muestra eventos **globales** (visibles para todos) y **personales** (dirigidos a un *mueble/dispositivo* concreto).
* Filtra por **región** (`location`) y por **audiencia** (`global`/`personal`).
* Soporta selección múltiple de destinatarios personales (query `targets=a,b,c`).
* Alta de eventos desde la web (requiere login): guarda en **MongoDB** con esquema flexible.
* API REST mínima con DRF basada en el modelo `Evento` de Django (base SQL), útil para integraciones.

> Nota de arquitectura: conviven **dos almacenes** de datos: (a) colección Mongo `eventos` consumida por las vistas HTML; (b) modelo SQL `Evento` servido por la API DRF. Mantener uno como **fuente de verdad** o añadir procesos de sincronización si ambos se usan en producción.

### 2) Módulos clave

* `models.py` → `Evento` (SQL) con `titulo`, `descripcion`, `fecha`, `location`, `created_by`.
* `serializers.py` → `EventoSerializer` (DRF) con `created_by` solo lectura.
* `views.py` →

  * **API DRF**: `EventoList(APIView)` (`GET` lista, `POST` crea con `created_by`=usuario autenticado).
  * **Frontend**: `home`, `lista_eventos`, `guardar_evento`, `extraer_evento`, `parse_response`, `app2`.
* `urls.py` → expone `EventoList` en la raíz del app.

### 3) Flujo frontend (Mongo)

1. **`home`**: carga mensaje de bienvenida y **regiones** distintas desde Mongo `eventos`.
2. **`lista_eventos`**: construye el filtro combinando:

   * `location`: `all` o una región concreta.
   * Visibilidad base: `audience in {all, missing}` + propios del usuario (`created_by`).
   * Si el usuario tiene `target_device` / `default_room`, incluye personales para ese destino.
   * **Modo** (`mode=global|personal`) y **targets** (`targets=a,b,c`). Soporta `device=` (legacy).
   * Genera paleta de colores determinista por `target_device`.
   * Devuelve a plantilla: `eventos`, `regiones`, `linked_device`, `mode_selected`, `selected_targets`, `my_devices`, `my_device_colors`.
3. **`guardar_evento`** (`POST`, login): recibe JSON `{title, date: 'YYYY-MM-DD', description, location, audience: 'all'|'device', target_device?}` y persiste en Mongo formateando `date` a `dd-mm-YYYY`. Si `audience='device'` y no llega `target_device`, usa el vinculado al usuario.
4. **`extraer_evento`** (`POST` con fichero `image`):

   * Guarda temporalmente la imagen en `MEDIA_ROOT/uploads/`.
   * Llama a **OpenAI** (modelo multimodal) para extraer `title`, `date`, `place`, `description`.
   * `parse_response` limpia y normaliza la fecha a `YYYY-MM-DD` (acepta formatos en español: "10 de marzo, 2025", etc.).

#### Esquema Mongo recomendado (`eventos`)

```json
{
  "title": "Texto",
  "date": "dd-mm-YYYY",
  "description": "Texto",
  "location": "Región/Barrio",
  "created_by": "<username>",
  "audience": "all" | "device",
  "target_device": "<id-mueble>" // si audience=='device'
}
```

#### Endpoints sugeridos (añadir en `urls.py` del proyecto)

```python
from apps.eventos import views as ev
urlpatterns = [
    # Páginas
    path('', ev.home, name='home'),
    path('eventos/', ev.lista_eventos, name='eventos'),
    path('app2/', ev.app2, name='app2'),

    # Acciones
    path('api/eventos/guardar/', ev.guardar_evento, name='guardar_evento'),
    path('api/eventos/extraer/', ev.extraer_evento, name='extraer_evento'),

    # API DRF (SQL)
    path('api/eventos/', include('apps.eventos.urls')),  # GET/POST lista
]
```

#### Ejemplos de uso

* **Listar eventos (Mongo HTML)**: `GET /eventos/?mode=personal&targets=salon,maria&location=centro`
* **Crear evento (Mongo)**:

```bash
curl -X POST /api/eventos/guardar/ \
  -H 'Content-Type: application/json' \
  -b 'sessionid=...' \
  -d '{
    "title":"Merienda",
    "date":"2025-10-28",
    "description":"En el centro",
    "location":"Labastida",
    "audience":"device",
    "target_device":"maria"
  }'
```

* **API DRF (SQL)**:

```bash
# List
GET /api/eventos/
# Crear (requiere auth DRF)
POST /api/eventos/ {"titulo":"...","descripcion":"...","fecha":"2025-10-29T16:00:00Z","location":"..."}
```

### 4) Variables y dependencias

* **MongoDB**: `MONGO_URI` (cadena Atlas).
* **OpenAI**: `OPENAI_API_KEY` (mover a settings; evitar hardcode). `MEDIA_ROOT` para subida temporal.
* **Django REST Framework** para la API.

### 5) Mejoras sugeridas

* Unificar fuente de datos (solo Mongo o solo SQL) o **sincronizarlas**.
* Mover `openai.api_key` a `settings`/env.
* Añadir índices Mongo: `{date:1}`, `{audience:1}`, `{target_device:1}`.
* Validaciones de servidor (fechas futuras, longitud de campos, etc.).

---

## README — Videollamada

### 1) Qué hace

* Genera **tokens de Twilio Video** (TTL 10 min) para un `identity` y `room_name`.
* Al generar token, publica **mensajes MQTT** en 3 *topics* para notificar al mueble y/o sistemas auxiliares.
* Vista `videocall` protegida; si no hay sesión, redirige a `login?next=...` con mensaje de aviso.
* Endpoint utilitario `toggle_emotion_detection` para activar/desactivar un proceso externo (escribe un flag en disco).

### 2) Módulos clave

* `views.py` → `generate_video_token`, `send_mqtt_notification`, `videocall`, `login_required_message`, `toggle_emotion_detection`.
* `templates/videocall.html` → UI cliente (unirse a sala usando token Twilio).

### 3) Flujo de llamada

1. Usuario autenticado entra en **/videocall** → plantilla recibe `identity` y `default_room` (si no existe, se setea tras generar token por primera vez).
2. Front solicita **token** a `/api/video/token/<identity>/<room>/` (o endpoint equivalente) → backend crea JWT con **Twilio** y lo devuelve como JSON.
3. Backend envía **MQTT**:

   * `calls/<room>` con payload JSON `{action:"incoming_call", room, from}`.
   * `MQTT_TOPIC_VIDEOCALL` (p.ej. `videollamada`) con `videollamada:<caller>` (compatibilidad con la app Kivy).
   * `MQTT_TOPIC_GENERAL` (p.ej. `tarjeta`) con `videollamada`.
4. El mueble escucha y abre UI de llamada; el front usa token para unirse a la **sala Twilio**.

### 4) Variables de entorno / `settings.py`

```python
# Twilio
TWILIO_ACCOUNT_SID = 'ACxxxxxxxx'
TWILIO_API_KEY     = 'SKxxxxxxxx'
TWILIO_API_SECRET  = 'xxxxxxxxxx'

# MQTT
MQTT_BROKER_URL   = 'broker.host'
MQTT_BROKER_PORT  = 1883
MQTT_USERNAME     = ''   # opcional
MQTT_PASSWORD     = ''   # opcional
MQTT_TOPIC_VIDEOCALL = 'videollamada'
MQTT_TOPIC_GENERAL   = 'tarjeta'
```

### 5) Rutas sugeridas

```python
from apps.eventos import views as ev
urlpatterns += [
    path('videocall/', ev.videocall, name='videocall'),
    path('api/video/token/<str:identity>/<str:room_name>/', ev.generate_video_token, name='video_token'),
    path('api/video/emotion-toggle/', ev.toggle_emotion_detection, name='emotion_toggle'),
]
```

### 6) Seguridad y notas

* Todas las rutas de vídeo deben requerir **HTTPS** y **usuario autenticado** (usar el decorador `login_required_message` o `@login_required`).
* No registrar en consola los JWT en producción.
* Configurar **CORS**/CSRF si hay cliente JS separado.
* `toggle_emotion_detection` usa una **ruta absoluta Windows**; extraer a `settings` y hacerla multiplataforma.

---

## Guía — Templates & CSS

### 1) Estructura

```
static/
└─ css/styles.css     # estilos de la app (eventos, videollamada)

templates/
├─ home.html          # bienvenida y filtro por regiones
├─ eventos.html       # calendario/lista + selectores (modo/targets)
├─ videocall.html     # UI de videollamada (Twilio)
└─ app2.html          # vista sencilla de prueba (i18n)
```

### 2) Convenciones

* Usa `{% load static %}` en cada template y enlaza `styles.css` desde un `base.html` o en cada página.
* Los contextos expuestos por las vistas (`eventos`, `regiones`, `mode_selected`, etc.) ya vienen listos para renderizar selectores/leyendas con color.
* Mantener los nombres de templates si se cambian las rutas para evitar `TemplateDoesNotExist`.

### 3) JS mínimo sugerido

* En `eventos.html`, manejar cambios de **modo** y **targets** actualizando la URL (query string) y recargando.
* Para **guardar**: `fetch('/api/eventos/guardar/', {method:'POST', headers:{'Content-Type':'application/json','X-CSRFToken':...}, body: JSON.stringify({...})})`.
* Para **extraer de imagen**: `FormData` con `image` → `/api/eventos/extraer/`.
* Para **videollamada**: solicitar token y unir a sala con el SDK de Twilio Video en el front.

---

## Checklist de configuración

* [ ] `apps.eventos` en `INSTALLED_APPS` y **DRF** instalado.
* [ ] `MONGO_URI` válido y **Mongo Atlas** accesible.
* [ ] `OPENAI_API_KEY` en entorno; `MEDIA_ROOT` configurado.
* [ ] Variables **Twilio** y **MQTT** definidas.
* [ ] URLs del proyecto incluyen rutas de **eventos** y **videollamada** (ver snippets).
* [ ] `LocaleMiddleware` y i18n habilitados si se usan textos traducidos en templates.

---

## Quickstart local

1. Exporta variables y arranca Mongo (o usa Atlas).
2. `pip install -r requirements.txt` (incluye `djangorestframework`, `pymongo`, `paho-mqtt`, `twilio`, `openai`).
3. Ejecuta migraciones (solo para el modelo SQL de `Evento` si vas a usar DRF): `python manage.py makemigrations && python manage.py migrate`.
4. `python manage.py runserver`.
5. Abre:

   * `/` → **home**.
   * `/eventos/` → listado y filtros.
   * `/videocall/` → UI videollamada (requiere sesión).
6. Prueba creación: POST a `/api/eventos/guardar/` con sesión activa.

---

> Cualquier ajuste de rutas o nombres de templates, recuerda actualizar los `reverse()`/URLs y comprobar los *names* usados en las vistas/templates.
