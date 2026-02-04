# App `eventos` — READMEs (EN)

> This app bundles **two main features** and part of the front end:
>
> 1. **Events** (calendar/list, create and filter) — persisted in **MongoDB** and exposes a DRF API for the Django `Evento` model (SQL).
> 2. **Video call** — integrates **Twilio Video** and publishes **MQTT notifications**.
>    It also contains most of the **HTML templates** and **CSS** for these screens.

---

## Index

* [README — Events](#readme--events)
* [README — Video call](#readme--video-call)
* [Guide — Templates & CSS](#guide--templates--css)
* [Configuration checklist](#configuration-checklist)
* [Local quickstart](#local-quickstart)

---

## README — Events

### 1) What it does

* Shows **global** events (visible to everyone) and **personal** events (targeted at a specific *device/furniture*).
* Filters by **region** (`location`) and **audience** (`global`/`personal`).
* Supports multi‑select of personal recipients (query `targets=a,b,c`).
* Create events from the web (requires login): stores in **MongoDB** with a flexible schema.
* Minimal REST API with DRF based on the Django `Evento` model (SQL), useful for integrations.

> Architecture note: there are **two data stores** living side by side: (a) Mongo collection `eventos` used by HTML views; (b) SQL model `Evento` served by the DRF API. Pick one as the **source of truth** or add sync processes if both are used in production.

### 2) Key modules

* `models.py` → `Evento` (SQL) with `titulo`, `descripcion`, `fecha`, `location`, `created_by`.
* `serializers.py` → `EventoSerializer` (DRF) with `created_by` read‑only.
* `views.py` →
  * **DRF API**: `EventoList(APIView)` (`GET` list, `POST` creates with `created_by` = authenticated user).
  * **Front end**: `home`, `lista_eventos`, `guardar_evento`, `extraer_evento`, `parse_response`, `app2`.
* `urls.py` → exposes `EventoList` at the app root.

### 3) Front‑end flow (Mongo)

1. **`home`**: loads a welcome message and distinct **regions** from Mongo `eventos`.
2. **`lista_eventos`**: builds the filter combining:
   * `location`: `all` or a specific region.
   * Base visibility: `audience in {all, missing}` + user’s own (`created_by`).
   * If the user has `target_device` / `default_room`, include personals for that target.
   * **Mode** (`mode=global|personal`) and **targets** (`targets=a,b,c`). Supports `device=` (legacy).
   * Generates a deterministic color palette by `target_device`.
   * Returns to the template: `eventos`, `regiones`, `linked_device`, `mode_selected`, `selected_targets`, `my_devices`, `my_device_colors`.
3. **`guardar_evento`** (`POST`, login): receives JSON `{title, date: 'YYYY-MM-DD', description, location, audience: 'all'|'device', target_device?}` and writes to Mongo formatting `date` as `dd-mm-YYYY`. If `audience='device'` and `target_device` is missing, uses the one linked to the user.
4. **`extraer_evento`** (`POST` with file `image`):
   * Temporarily stores the image under `MEDIA_ROOT/uploads/`.
   * Calls **OpenAI** (multimodal) to extract `title`, `date`, `place`, `description`.
   * `parse_response` cleans and normalizes the date to `YYYY-MM-DD` (accepts Spanish formats like “10 de marzo, 2025”).

#### Recommended Mongo schema (`eventos`)

```json
{
  "title": "Text",
  "date": "dd-mm-YYYY",
  "description": "Text",
  "location": "Region/Area",
  "created_by": "<username>",
  "audience": "all" | "device",
  "target_device": "<device-id>" // when audience=='device'
}
```

#### Suggested endpoints (add in project `urls.py`)

```python
from apps.eventos import views as ev
urlpatterns = [
    # Pages
    path('', ev.home, name='home'),
    path('eventos/', ev.lista_eventos, name='eventos'),
    path('app2/', ev.app2, name='app2'),

    # Actions
    path('api/eventos/guardar/', ev.guardar_evento, name='guardar_evento'),
    path('api/eventos/extraer/', ev.extraer_evento, name='extraer_evento'),

    # DRF API (SQL)
    path('api/eventos/', include('apps.eventos.urls')),  # GET/POST list
]
```

#### Usage examples

* **List events (Mongo HTML)**: `GET /eventos/?mode=personal&targets=livingroom,maria&location=center`
* **Create event (Mongo)**:

```bash
curl -X POST /api/eventos/guardar/   -H 'Content-Type: application/json'   -b 'sessionid=...'   -d '{
    "title":"Snack time",
    "date":"2025-10-28",
    "description":"At the center",
    "location":"Labastida",
    "audience":"device",
    "target_device":"maria"
  }'
```

* **DRF API (SQL)**:

```bash
# List
GET /api/eventos/
# Create (requires DRF auth)
POST /api/eventos/ {"titulo":"...","descripcion":"...","fecha":"2025-10-29T16:00:00Z","location":"..."}
```

### 4) Variables & dependencies

* **MongoDB**: `MONGO_URI` (Atlas connection string).
* **OpenAI**: `OPENAI_API_KEY` (move to settings; avoid hardcoding). `MEDIA_ROOT` for temporary uploads.
* **Django REST Framework** for the API.

### 5) Suggested improvements

* Consolidate the source of truth (only Mongo or only SQL) or **synchronize** both.
* Move `openai.api_key` to settings/env.
* Add Mongo indexes: `{date:1}`, `{audience:1}`, `{target_device:1}`.
* Server‑side validations (future dates, field length, etc.).

---

## README — Video call

### 1) What it does

* Generates **Twilio Video tokens** (TTL 10 min) for an `identity` and `room_name`.
* When generating a token, it publishes **MQTT messages** to 3 topics to notify the device and/or auxiliary systems.
* Protected `videocall` view; if there’s no session, redirects to `login?next=...` with a notice.
* Utility endpoint `toggle_emotion_detection` to enable/disable an external process (writes a flag to disk).

### 2) Key modules

* `views.py` → `generate_video_token`, `send_mqtt_notification`, `videocall`, `login_required_message`, `toggle_emotion_detection`.
* `templates/videocall.html` → client UI (join a room using a Twilio token).

### 3) Call flow

1. Authenticated user enters **/videocall** → template receives `identity` and `default_room` (if missing, it is set after the first token is generated).
2. Front end requests a **token** from `/api/video/token/<identity>/<room>/` (or similar) → backend creates a **Twilio** JWT and returns it as JSON.
3. Backend sends **MQTT**:
   * `calls/<room>` with JSON payload `{action:"incoming_call", room, from}`.
   * `MQTT_TOPIC_VIDEOCALL` (e.g., `videollamada`) with `videollamada:<caller>` (compat with the Kivy app).
   * `MQTT_TOPIC_GENERAL` (e.g., `tarjeta`) with `videollamada`.
4. The device listens and opens the call UI; the front end uses the token to join the **Twilio** room.

### 4) Environment variables / `settings.py`

```python
# Twilio
TWILIO_ACCOUNT_SID = 'ACxxxxxxxx'
TWILIO_API_KEY     = 'SKxxxxxxxx'
TWILIO_API_SECRET  = 'xxxxxxxxxx'

# MQTT
MQTT_BROKER_URL   = 'broker.host'
MQTT_BROKER_PORT  = 1883
MQTT_USERNAME     = ''   # optional
MQTT_PASSWORD     = ''   # optional
MQTT_TOPIC_VIDEOCALL = 'videollamada'
MQTT_TOPIC_GENERAL   = 'tarjeta'
```

### 5) Suggested routes

```python
from apps.eventos import views as ev
urlpatterns += [
    path('videocall/', ev.videocall, name='videocall'),
    path('api/video/token/<str:identity>/<str:room_name>/', ev.generate_video_token, name='video_token'),
    path('api/video/emotion-toggle/', ev.toggle_emotion_detection, name='emotion_toggle'),
]
```

### 6) Security & notes

* All video routes must require **HTTPS** and **authenticated users** (use `login_required_message` decorator or `@login_required`).
* Do not log JWTs in production.
* Configure **CORS**/CSRF if there is a separate JS client.
* `toggle_emotion_detection` uses a **Windows absolute path**; extract to `settings` and make it cross‑platform.

---

## Guide — Templates & CSS

### 1) Structure

```
static/
└─ css/styles.css     # app styles (events, video call)

templates/
├─ home.html          # welcome + region filter
├─ eventos.html       # calendar/list + selectors (mode/targets)
├─ videocall.html     # video call UI (Twilio)
└─ app2.html          # simple test view (i18n)
```

### 2) Conventions

* Use `{% load static %}` in each template and link `styles.css` from a `base.html` or per page.
* The contexts exposed by the views (`eventos`, `regiones`, `mode_selected`, etc.) are ready to render selectors/legends with color.
* Keep template names if you change routes to avoid `TemplateDoesNotExist`.

### 3) Minimal JS suggestions

* In `eventos.html`, handle **mode** and **targets** changes by updating the URL query string and reloading.
* To **save**: `fetch('/api/eventos/guardar/', {method:'POST', headers:{'Content-Type':'application/json','X-CSRFToken':...}, body: JSON.stringify({...})})`.
* To **extract from image**: `FormData` with `image` → `/api/eventos/extraer/`.
* For **video call**: request the token and join the room with the Twilio Video SDK on the front end.

---

## Configuration checklist

* [ ] `apps.eventos` in `INSTALLED_APPS` and **DRF** installed.
* [ ] Valid `MONGO_URI` and **Mongo Atlas** accessible.
* [ ] `OPENAI_API_KEY` in env; `MEDIA_ROOT` configured.
* [ ] **Twilio** and **MQTT** variables defined.
* [ ] Project URLs include **events** and **videocall** routes (see snippets).
* [ ] `LocaleMiddleware` and i18n enabled if templates use translated strings.

---

## Local quickstart

1. Export environment variables and start Mongo (or use Atlas).
2. `pip install -r requirements.txt` (includes `djangorestframework`, `pymongo`, `paho-mqtt`, `twilio`, `openai`).
3. Run migrations (only for the SQL `Evento` model if you’ll use DRF): `python manage.py makemigrations && python manage.py migrate`.
4. `python manage.py runserver`.
5. Open:
   * `/` → **home**.
   * `/eventos/` → list + filters.
   * `/videocall/` → video call UI (requires session).
6. Test creation: POST to `/api/eventos/guardar/` with an active session.

---

> If you change routes or template names, remember to update `reverse()`/URLs and check the *names* used across views/templates.
