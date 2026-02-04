# pizarra — README (EN)

> Simple messaging app (text + image) to send notes to the **device** ("mueble"), with per‑recipient history and an **incoming notifications** panel for the web user. Stores messages in **MongoDB** and images in **GridFS**.

---

## 1) What it does

* Send **short messages** (text and/or image) to a **recipient** viewed by the device app.
* Browse **message history** by recipient.
* Show **incoming notifications** (e.g., “Ready for call”).
* HTTP API for the **device** to fetch messages or **create notifications**.

---

## 2) Structure

```
apps/pizarra/
├─ apps.py              # AppConfig
├─ forms.py             # Validation (text or image, max 5MB)
├─ urls.py              # Page routes + APIs
├─ views.py             # Page views and JSON endpoints
└─ templates/pizarra/
   └─ pizarra.html      # Main UI (new message, history, notifications)
```

---

## 3) Dependencies & environment variables

**Required**

* `MONGO_URI` → MongoDB Atlas connection string.

**Optional**

* `DB_NAME` → database name (default: `LabasAppDB`).
* `NOTIFY_API_KEY` → shared key to protect `POST /pizarra/api/notify/`.
* `NOTIFY_TTL_HOURS` → default lifetime (hours) for each notification when `ttl_hours` isn’t provided in the POST. Default: `24`.

---

## 4) `settings.py` configuration

### 4.1 Apps, templates & i18n

```python
INSTALLED_APPS += [
    'apps.pizarra',
]

TEMPLATES[0]['OPTIONS']['context_processors'] += [
    'django.contrib.messages.context_processors.messages',
]

USE_I18N = True
LANGUAGES = (
    ('es', 'Español'),
    ('fr', 'Français'),
)
LOCALE_PATHS = [BASE_DIR / 'locale']
MIDDLEWARE += [
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
]
```

> The template uses Django’s `set_language` and the **messages** framework.

### 4.2 Variables

```python
MONGO_URI = os.getenv('MONGO_URI')
DB_NAME = os.getenv('DB_NAME', 'LabasAppDB')
NOTIFY_API_KEY = os.getenv('NOTIFY_API_KEY', '')
NOTIFY_TTL_HOURS = int(os.getenv('NOTIFY_TTL_HOURS', 24))
```

---

## 5) Project URLs

Add in the project `urls.py`:

```python
from django.urls import path, include

urlpatterns = [
    # ...
    path('pizarra/', include('apps.pizarra.urls')),
]
```

Main routes inside the app:

* Main page: `GET /pizarra/` → *pizarra_home*
* Create message: `POST /pizarra/nuevo/` → *pizarra_create*
* Serve image (GridFS): `GET /pizarra/img/<file_id>/` → *pizarra_image*
* Messages API (for the device): `GET /pizarra/api/messages/`
* Create notification (from the device): `POST /pizarra/api/notify/`
* Fetch notifications (web): `GET /pizarra/api/notifications/`
* Mark one as read: `POST /pizarra/notifications/mark-read/<notif_id>/`
* Mark all as read: `POST /pizarra/notifications/mark-all/`

---

## 6) Data & storage

### 6.1 Collections & GridFS

* **Messages**: collection `pizarra_messages`.
* **Notifications**: collection `pizarra_notifications`.
* **Images**: **GridFS** bucket `pizarra_fs`.

### 6.2 Indexes

* Notifications: compound index `(to_user, read, created_at)` for per‑user inbox.
* TTL: index `expire_at` (with `expireAfterSeconds=0`) to auto‑expire notifications.

### 6.3 Reference schemas

**Message**

```json
{
  "author": "web username",
  "recipient_key": "device/recipient key",
  "content": "optional text",
  "image_file_id": "ObjectId | null",
  "created_at": "ISODate"
}
```

**Notification**

```json
{
  "to_user": "web username",
  "from_device": "device/person identifier",
  "kind": "call_ready",  // default
  "message": "Disponible para llamada", // default text
  "created_at": "ISODate",
  "read": false,
  "expire_at": "ISODate | optional"
}
```

---

## 7) Views & validation

### 7.1 Form `PizarraPostForm`

* Requires **text** or **image** (at least one).
* **Image size limit = 5MB**.

### 7.2 `pizarra_create`

* If there’s an image, it’s saved in **GridFS** and referenced by `image_file_id`.
* Inserts a document in `pizarra_messages` and redirects back to the board.

### 7.3 `pizarra_image`

* Serves the GridFS image with `Content-Disposition: inline`.

### 7.4 `pizarra_home`

* Builds the **contacts** list for the user (profile + history) and loads **history** for `selected_contact`.
* Loads **unread notifications** and shows them in the side panel with a counter.

---

## 8) API endpoints (for the device)

### 8.1 Fetch messages

**GET** `/pizarra/api/messages/?recipient=<key>&since=<optional ISO8601>`

* `recipient` (required): recipient key used by the device.
* `since` (optional): ISO8601 (`...Z` or with offset) → only messages created after that timestamp.

**Example**

```bash
curl "https://your-domain.com/pizarra/api/messages/?recipient=livingroom&since=2025-01-01T00:00:00Z"
```

**Response**

```json
{
  "messages": [{
    "id": "...",
    "author": "...",
    "recipient": "...",
    "text": "...",
    "image": "https://.../pizarra/img/<file_id>/",
    "created_at": "2025-10-01T10:30:00+00:00"
  }]
}
```

### 8.2 Create notification

**POST** `/pizarra/api/notify/`

**Required header** if configured: `X-API-KEY: <NOTIFY_API_KEY>`

Accepted parameters (form‑data, x‑www‑form‑urlencoded or JSON):

* `to_user` (**required**): web user’s username.
* `from_device` (optional): device/person identifier.
* `kind` (optional, default `call_ready`).
* `message` (optional, default `Disponible para llamada`).
* `ttl_hours` (optional): **time‑to‑live** in hours; falls back to `NOTIFY_TTL_HOURS`.

**Example**

```bash
curl -X POST https://your-domain.com/pizarra/api/notify/ \
  -H "Content-Type: application/json" \
  -H "X-API-KEY: $NOTIFY_API_KEY" \
  -d '{
    "to_user": "jaime",
    "from_device": "livingroom",
    "kind": "call_ready",
    "message": "Call now?",
    "ttl_hours": 12
  }'
```

**Response**

```json
{ "ok": true, "id": "<inserted_id>" }
```

### 8.3 Fetch notifications (web)

**GET** `/pizarra/api/notifications/?only_unread=1`

* Returns up to 100 notifications (by default only **unread**). Use `only_unread=0` for all.

### 8.4 Mark notifications

* **Single**: `POST /pizarra/notifications/mark-read/<id>/`
* **All**: `POST /pizarra/notifications/mark-all/`

---

## 9) Interface (template)

* **Recipient selector**: chips + `datalist` for autocomplete.
* **New message**: textarea + image input with **preview** (vanilla JS).
* **History**: list of messages (text + thumbnail when there’s an image).
* **Notifications panel**: with **counter** and actions (mark one / mark all, plus a CTA to video call).
* Top navigation (Events, Video calls, Pizarra), with **ES/FR** language selector.

> The UI relies on generic classes (`.pz-card`, `.history-list`, `.notif-list`, etc.). Adjust the project CSS if you change the layout.

---

## 10) End‑to‑end flow

1. Web user signs in and opens **/pizarra/**.
2. Selects or types a **recipient**.
3. Sends **text** and/or **image** (≤ 5MB). Image is stored in **GridFS**.
4. The device periodically calls **/api/messages** with its `recipient` key.
5. When the device needs to alert the web user (e.g., “ready for a call”), it calls **/api/notify**.
6. The user sees **notifications** on the side panel and can **mark them**.

---

## 11) Security & best practices

* Protect **/api/notify/** with `NOTIFY_API_KEY` and enforce **HTTPS**.
* Consider max upload size at reverse proxy level and add **rate‑limiting** if needed.
* Ensure `LocaleMiddleware` and `LANGUAGES` are enabled for ES/FR switching.
* JSON endpoints are **CSRF‑exempt** only for `api_notify` (external client). Others require session/CSRF.

---

## 12) Quick checklist

* [ ] `apps.pizarra` added to `INSTALLED_APPS`.
* [ ] `MONGO_URI` (and optional `DB_NAME`) configured.
* [ ] `NOTIFY_API_KEY` and `NOTIFY_TTL_HOURS` (optional) defined.
* [ ] `LocaleMiddleware` and `LANGUAGES` (es/fr) set in settings.
* [ ] Project URL `path('pizarra/', include('apps.pizarra.urls'))` added.
* [ ] Project styles updated for `.pz-*` classes.

---

## 13) Future work

* WebSockets/Server‑Sent Events for **real‑time notifications**.
* Infinite scroll for **history** and text search.
* Message moderation/antispam.
* **MQTT** integration for device events, bridging to `api_notify`.

## 14) Extra

* Example of document for adding a notification in MongoDb table for testing:

{
  "to_user": "TU_USERNAME_WEB",
  "from_device": "USERNAME_MUEBLE",
  "kind": "call_ready",
  "message": "Estoy disponible para hablar",
  "created_at": { "$date": "2025-10-30T12:00:00Z" },
  "read": false,
  "expire_at": { "$date": "2025-10-31T12:00:00Z" }
}
