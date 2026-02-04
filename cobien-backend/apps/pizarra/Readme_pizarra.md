# pizarra — README

> App de mensajería simple (texto + imagen) para enviar notas al “mueble” (dispositivo), con histórico por destinatario y bandeja de **notificaciones entrantes** para el usuario web. Almacena mensajes en **MongoDB** y las imágenes en **GridFS**.

---

## 1) Qué resuelve

* Enviar **mensajes breves** (texto e/ó imagen) al **destinatario** que verá la app del mueble.
* Consultar **historial** por destinatario.
* Mostrar **notificaciones entrantes** (p. ej. “Disponible para llamada”).
* API HTTP para que el **mueble** recupere mensajes o **cree notificaciones**.

---

## 2) Estructura relevante

```
apps/pizarra/
├─ apps.py              # AppConfig
├─ forms.py             # Validación (texto o imagen, máx 5MB)
├─ urls.py              # Rutas de páginas + APIs
├─ views.py             # Vistas de página y endpoints JSON
└─ templates/pizarra/
   └─ pizarra.html      # UI principal (nuevo mensaje, historial, notifs)
```

---

## 3) Dependencias y variables de entorno

**Requeridas**

* `MONGO_URI` → cadena de conexión MongoDB Atlas.

**Opcionales**

* `DB_NAME` → nombre de base de datos (por defecto: `LabasAppDB`).
* `NOTIFY_API_KEY` → clave compartida para proteger `POST /pizarra/api/notify/`.
* `NOTIFY_TTL_HOURS` → horas de vida por defecto de cada notificación (si no se envía `ttl_hours` en el POST). Por defecto: `24`.

---

## 4) Configuración en `settings.py`

### 4.1 Apps, templates e i18n

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

> La plantilla usa el `set_language` de Django y el framework de **messages**.

### 4.2 Variables

```python
MONGO_URI = os.getenv('MONGO_URI')
DB_NAME = os.getenv('DB_NAME', 'LabasAppDB')
NOTIFY_API_KEY = os.getenv('NOTIFY_API_KEY', '')
NOTIFY_TTL_HOURS = int(os.getenv('NOTIFY_TTL_HOURS', 24))
```

---

## 5) URLs del proyecto

Añade en `urls.py` del proyecto:

```python
from django.urls import path, include

urlpatterns = [
    # ...
    path('pizarra/', include('apps.pizarra.urls')),
]
```

Rutas principales dentro de la app:

* Página principal: `GET /pizarra/` → *pizarra_home*
* Enviar nuevo mensaje: `POST /pizarra/nuevo/` → *pizarra_create*
* Servir imagen (GridFS): `GET /pizarra/img/<file_id>/` → *pizarra_image*
* API mensajes (para el mueble): `GET /pizarra/api/messages/`
* API crear notificación (desde el mueble): `POST /pizarra/api/notify/`
* API consultar notificaciones (web): `GET /pizarra/api/notifications/`
* Marcar leída: `POST /pizarra/notifications/mark-read/<notif_id>/`
* Marcar todas: `POST /pizarra/notifications/mark-all/`

---

## 6) Datos y almacenamiento

### 6.1 Colecciones y GridFS

* **Mensajes**: colección `pizarra_messages`.
* **Notificaciones**: colección `pizarra_notifications`.
* **Imágenes**: **GridFS** en bucket `pizarra_fs`.

### 6.2 Índices

* Notificaciones: índice compuesto `(to_user, read, created_at)` para bandeja por usuario.
* TTL: índice `expire_at` (con `expireAfterSeconds=0`) para autoexpirar notificaciones.

### 6.3 Esquemas orientativos

**Mensaje**

```json
{
  "author": "username web",
  "recipient_key": "destinatario/mueble",
  "content": "texto opcional",
  "image_file_id": "ObjectId | null",
  "created_at": "ISODate"
}
```

**Notificación**

```json
{
  "to_user": "username web",
  "from_device": "identificador mueble/persona",
  "kind": "call_ready",  // por defecto
  "message": "Disponible para llamada", // por defecto
  "created_at": "ISODate",
  "read": false,
  "expire_at": "ISODate | opcional"
}
```

---

## 7) Vistas y validaciones

### 7.1 Formulario `PizarraPostForm`

* Requiere **texto** o **imagen** (al menos uno).
* Límite de **imagen = 5MB**.

### 7.2 `pizarra_create`

* Si hay imagen, se guarda en **GridFS** y se referencia mediante `image_file_id`.
* Inserta un documento en `pizarra_messages` y redirige de nuevo a la pizarra.

### 7.3 `pizarra_image`

* Sirve la imagen almacenada en GridFS con `Content-Disposition: inline`.

### 7.4 `pizarra_home`

* Construye lista de **contactos** del usuario (perfil + históricos) y carga **historial** del `selected_contact`.
* Carga **notificaciones no leídas** y las muestra en lateral con contador.

---

## 8) Endpoints API (para el mueble)

### 8.1 Obtener mensajes

**GET** `/pizarra/api/messages/?recipient=<clave>&since=<ISO8601 opcional>`

* `recipient` (obligatorio): clave del destinatario que usa el mueble.
* `since` (opcional): ISO8601 (`...Z` o con offset) → limita a mensajes posteriores.

**Ejemplo**

```bash
curl "https://tu-dominio.com/pizarra/api/messages/?recipient=salon&since=2025-01-01T00:00:00Z"
```

**Respuesta**

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

### 8.2 Crear notificación

**POST** `/pizarra/api/notify/`

Cabecerá **obligatoria** si está configurada: `X-API-KEY: <NOTIFY_API_KEY>`

Parámetros admitidos (form-data, x-www-form-urlencoded o JSON):

* `to_user` (**obligatorio**): username del usuario web.
* `from_device` (opcional): identificador del mueble/persona.
* `kind` (opcional, por defecto `call_ready`).
* `message` (opcional, por defecto `Disponible para llamada`).
* `ttl_hours` (opcional): **horas de vida**; si no se pasa, usa `NOTIFY_TTL_HOURS`.

**Ejemplo**

```bash
curl -X POST https://tu-dominio.com/pizarra/api/notify/ \
  -H "Content-Type: application/json" \
  -H "X-API-KEY: $NOTIFY_API_KEY" \
  -d '{
    "to_user": "jaime",
    "from_device": "salon",
    "kind": "call_ready",
    "message": "¿Hablamos?",
    "ttl_hours": 12
  }'
```

**Respuesta**

```json
{ "ok": true, "id": "<inserted_id>" }
```

### 8.3 Consultar notificaciones (web)

**GET** `/pizarra/api/notifications/?only_unread=1`

* Devuelve hasta 100 notificaciones (por defecto solo **no leídas**). Usa `only_unread=0` para todas.

### 8.4 Marcar notificaciones

* **Una**: `POST /pizarra/notifications/mark-read/<id>/`
* **Todas**: `POST /pizarra/notifications/mark-all/`

---

## 9) Interfaz (plantilla)

* **Selector de destinatario**: chips + `datalist` para autocompletar.
* **Nuevo mensaje**: textarea + input de imagen con **preview** (JS nativo).
* **Historial**: lista de mensajes (texto + miniatura si hay imagen).
* **Lateral de notificaciones**: con **contador** y acciones (marcar leída / todas, y CTA a videollamada).
* Navegación superior (Eventos, Videollamadas, Pizarra), con selector de **idioma ES/FR**.

> La UI usa clases genéricas (`.pz-card`, `.history-list`, `.notif-list`, etc.). Ajusta el CSS del proyecto si cambias la maqueta.

---

## 10) Flujo funcional (end‑to‑end)

1. Usuario web inicia sesión y accede a **/pizarra/**.
2. Selecciona o escribe un **destinatario**.
3. Envía **texto** y/o **imagen** (≤ 5MB). La imagen se guarda en **GridFS**.
4. El mueble consulta periódicamente **/api/messages** con su `recipient`.
5. Cuando el mueble quiera avisar al usuario web (p. ej. “listo para llamada”), llama a **/api/notify**.
6. El usuario ve las **notificaciones** en el lateral y puede **marcarlas**.

---

## 11) Seguridad y buenas prácticas

* Protege **/api/notify/** con `NOTIFY_API_KEY` y usa **HTTPS**.
* Considera limitar tamaño de carga a nivel de servidor (reverse proxy) y añadir **rate‑limit** si fuera necesario.
* Verifica que `LocaleMiddleware` y `LANGUAGES` estén activos para el cambio ES/FR.
* Los endpoints JSON son **sin CSRF** solo en `api_notify` (porque lo llama un cliente externo). El resto requiere sesión/CSRF.

---

## 12) Checklist rápido

* [ ] `apps.pizarra` en `INSTALLED_APPS`.
* [ ] `MONGO_URI` (y opcional `DB_NAME`) configurados.
* [ ] `NOTIFY_API_KEY` y `NOTIFY_TTL_HOURS` (opcional) definidos.
* [ ] `LocaleMiddleware` y `LANGUAGES` (es/fr) en settings.
* [ ] Ruta `path('pizarra/', include('apps.pizarra.urls'))` añadida.
* [ ] CSS/estilos del proyecto ajustados para las clases `.pz-*`.

---

## 13) Extensiones futuras

* WebSockets/Server‑Sent Events para **notificaciones en tiempo real**.
* Paginación infinita del **historial** y búsqueda por texto.
* Moderación/antispam en mensajes.
* Integración con **MQTT** para eventos del mueble y puenteo hacia `api_notify`.

## 14) Extra

* Ejemplo de documento a insertar en MongoDb para probar notificaciones:

{
  "to_user": "TU_USERNAME_WEB",
  "from_device": "USERNAME_MUEBLE",
  "kind": "call_ready",
  "message": "Estoy disponible para hablar",
  "created_at": { "$date": "2025-10-30T12:00:00Z" },
  "read": false,
  "expire_at": { "$date": "2025-10-31T12:00:00Z" }
}
