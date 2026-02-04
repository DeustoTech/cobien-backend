# CoBien — General README (clean version)

Django 5 project with **MongoDB Atlas**, **MQTT**, video calls using **Twilio Video**, and deployment on **Render**. 
Includes i18n (ES/FR) and the apps: `accounts`, `eventos` (events + video call), `emociones` (AI), `pizarra`, `asociacion`.

> This README summarizes the essentials and **delegates technical details** to the specific READMEs for each area to keep it lightweight.

---

## Table of contents
1. [Stack & requirements](#stack--requirements)
2. [Repository structure](#repository-structure)
3. [Local quickstart](#local-quickstart)
4. [Configuration & environment variables](#configuration--environment-variables)
5. [Apps & associated docs](#apps--associated-docs)
6. [Deployment on Render](#deployment-on-render)
7. [Useful routes](#useful-routes)
8. [Development & testing](#development--testing)
9. [Troubleshooting](#troubleshooting)
10. [Related documents](#related-documents)

---

## Stack & requirements
- **Python** 3.11+ (64‑bit)
- **Django** 5
- **MongoDB Atlas** (or compatible)
- **Twilio Video**, **MQTT**
- **Git** and **pip**

> For precise versions and deeper settings (i18n, static files, email, proxy/security), see the **settings README**.

---

## Repository structure
```
backend-web/
└─ cobien-backend/
   ├─ apps/
   │  ├─ accounts/
   │  ├─ eventos/           # events + Twilio views + static
   │  ├─ emociones/         # emotion endpoints + local scripts
   │  ├─ pizarra/
   │  └─ asociacion/
   ├─ cobien/               # settings/urls/wsgi
   ├─ locale/               # es/ fr/ (.po/.mo)
   ├─ media/                # temporary uploads
   ├─ scripts/
   ├─ manage.py
   └─ requirements.txt
```

---

## Local quickstart
```bash
# 1) Virtual environment
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

# 2) Dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 3) Migrations + superuser
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser

# 4) Run
python manage.py runserver 0.0.0.0:8000
```
> Required variables and `.env` examples: see **ReadmeSettings.md** / **ReadmeSettings_EN.md**.

---

## Configuration & environment variables
To avoid duplication, all configuration (Django core, Mongo, Resend, Twilio, MQTT, i18n, static/WhiteNoise, security/CSRF) is documented in:
- **ReadmeSettings.md** (ES) / **ReadmeSettings_EN.md** (EN)

It also includes:
- A template for **per‑environment credentials** (DEV/PROD).
- Commands to set `MONGO_URI` in **PowerShell** (local work).
- A guide to **authorize IP** in **MongoDB Atlas**.

---

## Apps & associated docs
- **accounts** → signup/login, email activation, password reset, language preference.  
  _See_: **ReadmeAccounts.md** / **ReadmeAccounts_EN.md**

- **eventos / videollamada / templates** → events (Mongo) + DRF API (`Evento`), Twilio Video + MQTT, templates & CSS guide.  
  _See_: **ReadmeMultiApp.md** / **ReadmeMultiApp_EN.md**

- **emociones** → ROI/detect/finalize endpoints (summary in Mongo) + local scripts (`detector_*`, `uploader_mongo.py`).  
  _See_: **ReadmeEmociones.md** / **ReadmeEmociones_EN.md**

> Each app README contains **endpoints, payloads, flows**, and security/improvement notes.

---

## Deployment on Render
Condensed steps (details in ReadmeSettings.md):
1. **Root Directory**: `backend-web/cobien-backend`
2. **Build**: `pip install -r requirements.txt && python manage.py collectstatic --noinput`
3. **Start**: `gunicorn cobien.wsgi --bind 0.0.0.0:$PORT`
4. **ENV**: `DEBUG=False`, `ALLOWED_HOSTS`, `RENDER_EXTERNAL_URL` and variables for **Mongo/Resend/Twilio/MQTT**.
5. Ensure `collectstatic` succeeds and `ALLOWED_HOSTS`/CSRF are correctly set.

---

## Useful routes
- `/admin/` — Django admin
- `/` — Home (events)
- `/eventos/` — list & filters
- `/videocall/` — video call (login required)
- `/accounts/password_reset/` … (complete flow)
- `/signup/`, `/login/`, `/logout/`, `/activate/<uid>/<token>/`
- `/emociones/` — emotion API (`seleccionar`, `detectar`, `finalizar`)

---

## Development & testing
- Tests: `python manage.py test`
- i18n: `django-admin makemessages -l es -l fr && django-admin compilemessages`
- Working with Mongo locally → use Atlas and add your **IP** to the Access List (see ReadmeSettings.md).

---

## Troubleshooting
To avoid repetition, consult the **FAQ section** in **ReadmeSettings.md** (CSS/JS in prod, CSRF/Render, translations, emails, Twilio token, MQTT).

---

## Related documents
- **ReadmeSettings.md** / **ReadmeSettings_EN.md**
- **ReadmeAccounts.md** / **ReadmeAccounts_EN.md**
- **ReadmeMultiApp.md** / **ReadmeMultiApp_EN.md**
- **ReadmeEmociones.md** / **ReadmeEmociones_EN.md**
- **ReadmeGeneral_EN.md** (English version of the full general README)
