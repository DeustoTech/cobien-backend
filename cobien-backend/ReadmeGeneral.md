# CoBien — README general (versión limpia)

Proyecto Django 5 con **MongoDB Atlas**, **MQTT**, videollamadas con **Twilio Video** y despliegue en **Render**. 
Incluye i18n (ES/FR) y las apps: `accounts`, `eventos` (eventos + videollamada), `emociones` (IA), `pizarra`, `asociacion`.

> Este README resume lo esencial y **delegará los detalles técnicos** en los READMEs específicos de cada área para mantenerlo ligero.

---

## Índice
1. [Stack y requisitos](#stack-y-requisitos)
2. [Estructura del repositorio](#estructura-del-repositorio)
3. [Inicio rápido en local](#inicio-rápido-en-local)
4. [Configuración y variables de entorno](#configuración-y-variables-de-entorno)
5. [Apps y documentación asociada](#apps-y-documentación-asociada)
6. [Despliegue en Render](#despliegue-en-render)
7. [Rutas útiles](#rutas-útiles)
8. [Desarrollo y pruebas](#desarrollo-y-pruebas)
9. [Resolución de problemas](#resolución-de-problemas)
10. [Documentos relacionados](#documentos-relacionados)

---

## Stack y requisitos
- **Python** 3.11+ (64‑bit)
- **Django** 5
- **MongoDB Atlas** (o compatible)
- **Twilio Video**, **MQTT**
- **Git** y **pip**

> Para versiones y ajustes finos (i18n, estáticos, email, seguridad proxy), consulta el **README de settings**.

---

## Estructura del repositorio
```
backend-web/
└─ cobien-backend/
   ├─ apps/
   │  ├─ accounts/
   │  ├─ eventos/           # eventos + vistas Twilio + estáticos
   │  ├─ emociones/         # endpoints IA + scripts locales
   │  ├─ pizarra/
   │  └─ asociacion/
   ├─ cobien/               # settings/urls/wsgi
   ├─ locale/               # es/ fr/ (.po/.mo)
   ├─ media/                # subidas temporales
   ├─ scripts/
   ├─ manage.py
   └─ requirements.txt
```

---

## Inicio rápido en local
```bash
# 1) Entorno virtual
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

# 2) Dependencias
pip install --upgrade pip
pip install -r requirements.txt

# 3) Migraciones + superusuario
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser

# 4) Run
python manage.py runserver 0.0.0.0:8000
```
> Variables necesarias y ejemplos de `.env`: ver **ReadmeSettings.md**.

---

## Configuración y variables de entorno
Para no duplicar, toda la configuración (Django core, Mongo, Resend, Twilio, MQTT, i18n, estáticos/WhiteNoise, seguridad/CSRF) está documentada en:
- **ReadmeSettings.md** (ES) / **ReadmeSettings_EN.md** (EN)

Incluye además:
- Plantilla para **credenciales por entorno** (DEV/PROD).
- Comandos para definir `MONGO_URI` en **PowerShell** (local).
- Guía para **autorizar IP** en **MongoDB Atlas**.

---

## Apps y documentación asociada
- **accounts** → alta/login, activación por email, reset, preferencia de idioma.  
  _Ver_: **ReadmeAccounts.md** / **ReadmeAccounts_EN.md**

- **eventos / videollamada / templates** → eventos (Mongo) + API DRF (`Evento`), Twilio Video + MQTT, guía de templates y CSS.  
  _Ver_: **ReadmeMultiApp.md** / **ReadmeMultiApp_EN.md**

- **emociones** → endpoints ROI/detección/finalización (resumen en Mongo) + scripts locales (`detector_*`, `uploader_mongo.py`).  
  _Ver_: **ReadmeEmociones.md** / **ReadmeEmociones_EN.md**

> Cada README de app contiene los **endpoints, payloads, flujos** y notas de seguridad/mejoras.

---

## Despliegue en Render
Pasos resumidos (detalles en ReadmeSettings.md):
1. **Root Directory**: `backend-web/cobien-backend`
2. **Build**: `pip install -r requirements.txt && python manage.py collectstatic --noinput`
3. **Start**: `gunicorn cobien.wsgi --bind 0.0.0.0:$PORT`
4. **ENV**: `DEBUG=False`, `ALLOWED_HOSTS`, `RENDER_EXTERNAL_URL` y variables de **Mongo/Resend/Twilio/MQTT**.
5. Verifica que `collectstatic` no falle y que `ALLOWED_HOSTS`/CSRF estén bien definidos.

---

## Rutas útiles
- `/admin/` — Admin Django
- `/` — Home (eventos)
- `/eventos/` — listado y filtros
- `/videocall/` — videollamada (login requerido)
- `/accounts/password_reset/` … (flujo completo)
- `/signup/`, `/login/`, `/logout/`, `/activate/<uid>/<token>/`
- `/emociones/` — API emocional (`seleccionar`, `detectar`, `finalizar`)

---

## Desarrollo y pruebas
- Tests: `python manage.py test`
- i18n: `django-admin makemessages -l es -l fr && django-admin compilemessages`
- Trabajar con Mongo en local → usar Atlas añadiendo tu **IP** a la Access List (ver ReadmeSettings.md).

---

## Resolución de problemas
Para no repetir, consulta la **sección FAQ** en **ReadmeSettings.md** (CSS/JS en prod, CSRF/Render, traducciones, correos, Twilio token, MQTT).

---

## Documentos relacionados
- **ReadmeSettings.md** / **ReadmeSettings_EN.md**
- **ReadmeAccounts.md** / **ReadmeAccounts_EN.md**
- **ReadmeMultiApp.md** / **ReadmeMultiApp_EN.md**
- **ReadmeEmociones.md** / **ReadmeEmociones_EN.md**
- **ReadmeGeneral_EN.md** (versión inglesa de este general)
