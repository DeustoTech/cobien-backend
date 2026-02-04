# README — `settings.py` for the CoBien Project

This document explains **what** the project's `cobien/settings.py` does and **how to configure it**. It includes:
- A quick map of file sections and their purpose.
- Environment variables and defaults.
- **MongoDB** (djongo) configuration and how to connect **locally** (includes a PowerShell command).
- Transactional email (Resend), **Twilio Video**, **MQTT**, static files/WhiteNoise, i18n (ES/FR), and production security on **Render**.
- A template to **paste accounts/passwords** per environment (do **not** commit this).
- A guide to **authorize your IP** in MongoDB Atlas.

> **File path**: `backend-web/cobien-backend/cobien/settings.py`.

---

## 1) Map of `settings.py` (what each block does)

- **Base vars**: `BASE_DIR`, `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`.
- **INSTALLED_APPS**: project apps (`apps.eventos`, `apps.asociacion`, `apps.emociones`, `apps.accounts`, `apps.pizarra`) + Django core + `rest_framework`.
- **MIDDLEWARE**: includes `WhiteNoise` to serve static files in production.
- **TEMPLATES**, `ROOT_URLCONF`, `WSGI_APPLICATION`.
- **DATABASES**: uses **djongo** pointing to **MongoDB Atlas** via `MONGO_URI` (and `DB_NAME`).
- **Auth**: password validators, `AUTHENTICATION_BACKENDS` (email/username login), login/logout URLs.
- **i18n**: ES/FR with `LocaleMiddleware`, `LOCALE_PATHS=locale/`.
- **Static & Media**: `STATIC_URL`, `STATICFILES_DIRS` (`apps/eventos/static`), `STATIC_ROOT`, `MEDIA_ROOT`, storage via `WhiteNoise` in prod.
- **Email**: **Resend** SMTP using `RESEND_API_KEY` and `DEFAULT_FROM_EMAIL`.
- **Twilio**: `TWILIO_ACCOUNT_SID`, `TWILIO_API_KEY`, `TWILIO_API_SECRET`, etc.
- **MQTT**: `MQTT_BROKER_URL`, `MQTT_TOPIC_*`.
- **Security/Proxy**: `CSRF_TRUSTED_ORIGINS` (in production), `SECURE_PROXY_SSL_HEADER`, `USE_X_FORWARDED_HOST`.

---

## 2) Environment variables used

> Define these variables in your environment (Render or local `.env`). Where the code has a fallback, **override it** in production.

### Django core
- `DJANGO_SECRET_KEY` — **Required in production**.
- `DEBUG` — `"True"`/`"False"`. On Render: **False**.
- `ALLOWED_HOSTS` — comma‑separated list (e.g., `example.com,.onrender.com`).
- `RENDER_EXTERNAL_URL` — public Render URL for CSRF (prod only).

### Database (MongoDB via djongo)
- `DB_NAME` — e.g., `LabasAppDB` (code default).
- `MONGO_URI` — Atlas connection string (see section 4).

### Email (Resend)
- `RESEND_API_KEY` — API key.
- `DEFAULT_FROM_EMAIL` — e.g., `CoBien <no-reply@portal.co-bien.eu>`.

### Twilio (Video)
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN` *(if used)*
- `TWILIO_API_KEY`
- `TWILIO_API_SECRET`
- `TWILIO_CONVERSATION_SERVICE_SID` *(optional)*

### MQTT
- `MQTT_BROKER_URL` — e.g., `broker.hivemq.com`
- `MQTT_BROKER_PORT` — e.g., `1883`
- `MQTT_USERNAME` / `MQTT_PASSWORD` *(if applicable)*
- `MQTT_TOPIC_GENERAL` — default `tarjeta`
- `MQTT_TOPIC_VIDEOCALL` — default `videollamada`

---

## 3) Secure credentials template per environment

> **Do not commit this block with real values.** Store it in a secrets manager or in **Render → Environment**.  
> Copy this schema and fill it out for **DEV / STAGING / PROD**.

```text
====================
ENVIRONMENT: DEVELOPMENT
====================
Django
- DJANGO_SECRET_KEY: ********************************
- DEBUG: True
- ALLOWED_HOSTS: localhost,127.0.0.1

MongoDB Atlas
- DB_NAME: LabasAppDB
- MONGO_URI: mongodb+srv://<user>:<password>@<cluster>/?retryWrites=true&w=majority&appName=<AppName>

Resend (email)
- RESEND_API_KEY: ****************************************
- DEFAULT_FROM_EMAIL: CoBien <no-reply@portal.co-bien.eu>

Twilio
- TWILIO_ACCOUNT_SID: AC********************************
- TWILIO_API_KEY: SK********************************
- TWILIO_API_SECRET: ********************************
- TWILIO_AUTH_TOKEN: (if applicable)
- TWILIO_CONVERSATION_SERVICE_SID: (optional)

MQTT
- MQTT_BROKER_URL: broker.hivemq.com
- MQTT_BROKER_PORT: 1883
- MQTT_USERNAME: (if applicable)
- MQTT_PASSWORD: (if applicable)
- MQTT_TOPIC_GENERAL: tarjeta
- MQTT_TOPIC_VIDEOCALL: videollamada


====================
ENVIRONMENT: PRODUCTION
====================
Django
- DJANGO_SECRET_KEY: ********************************
- DEBUG: False
- ALLOWED_HOSTS: <your-app>.onrender.com,<custom-domain>
- RENDER_EXTERNAL_URL: https://<your-service>.onrender.com

MongoDB Atlas
- DB_NAME: LabasAppDB
- MONGO_URI: mongodb+srv://<user>:<password>@<cluster>/?retryWrites=true&w=majority&appName=<AppName>

Resend (email)
- RESEND_API_KEY: ****************************************
- DEFAULT_FROM_EMAIL: CoBien <no-reply@portal.co-bien.eu>

Twilio
- (same keys as above, but PROD values)

MQTT
- (same variables, pointing to PROD broker if applicable)
```

> **Tip**: keep a private `env.template` with **masked** keys and a local `.env` that is **not versioned**.

---

## 4) Connecting to **MongoDB** (local & production)

The project uses **djongo** with a Mongo client configured like this under `DATABASES['default']`:
```python
'ENGINE': 'djongo',
'NAME': os.getenv('DB_NAME', 'LabasAppDB'),
'CLIENT': {'host': os.getenv('MONGO_URI', '<default-string>')}
```

### 4.1 Connect **locally** (Windows PowerShell)
Run this command (requested example):
```powershell
$env:MONGO_URI = 'mongodb+srv://usuarioCoBien:passwordCoBien@clustercobienevents.j8ev5.mongodb.net/?retryWrites=true&w=majority&appName=ClusterCoBienEvents'
```
Optionally set:
```powershell
$env:DB_NAME = 'LabasAppDB'
$env:DEBUG = 'True'
```

### 4.2 Connect **locally** (macOS/Linux bash)
```bash
export MONGO_URI='mongodb+srv://usuarioCoBien:passwordCoBien@clustercobienevents.j8ev5.mongodb.net/?retryWrites=true&w=majority&appName=ClusterCoBienEvents'
export DB_NAME='LabasAppDB'
export DEBUG=True
```

> After setting the variables, start the server:
> ```bash
> python manage.py runserver
> ```

### 4.3 Atlas IP authorization (required)
If you get `IP not allowed` or `unable to connect`, proceed to **Section 10**.

---

## 5) i18n (ES/FR)

- `LANGUAGE_CODE='es'`, `LANGUAGES=[('es','Español'),('fr','Français')]`.
- Ensure `LocaleMiddleware` is in `MIDDLEWARE` and the `locale/` folder exists.
- Commands:
  ```bash
  django-admin makemessages -l es -l fr
  django-admin compilemessages
  ```

> **Time zone**: `TIME_ZONE='UTC'` in `settings.py`. If you need local time in views/templates, adjust `TIME_ZONE` or convert at the presentation layer.

---

## 6) Static files & media

- In production, **WhiteNoise** is used:
  ```python
  MIDDLEWARE: 'whitenoise.middleware.WhiteNoiseMiddleware'
  STATICFILES_STORAGE='whitenoise.storage.CompressedManifestStaticFilesStorage'
  ```
- Directories:
  - `STATICFILES_DIRS = ['apps/eventos/static']`
  - `STATIC_ROOT = 'staticfiles'`
  - `MEDIA_ROOT = 'media'`
- Remember to run in prod:
  ```bash
  python manage.py collectstatic --noinput
  ```

---

## 7) Email (Resend) & Twilio

### Email via Resend
```python
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.resend.com"
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = "resend"
EMAIL_HOST_PASSWORD = os.getenv("RESEND_API_KEY")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "CoBien <no-reply@portal.co-bien.eu>")
```
- In local dev, if you want to see emails in the console:
  ```python
  EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
  ```

### Twilio Video
Variables are read from ENV: `TWILIO_ACCOUNT_SID`, `TWILIO_API_KEY`, `TWILIO_API_SECRET`, etc.  
Do **not** leave defaults in production.

---

## 8) MQTT

Define broker, port, and credentials via ENV; default topics are `tarjeta` (general) and `videollamada` for calls.

```python
MQTT_BROKER_URL, MQTT_BROKER_PORT, MQTT_USERNAME, MQTT_PASSWORD
MQTT_TOPIC_GENERAL, MQTT_TOPIC_VIDEOCALL
```

---

## 9) Security & deployment (Render)

- `DEBUG=False` in production.
- `ALLOWED_HOSTS` must include your Render domain and any custom domain.
- `CSRF_TRUSTED_ORIGINS` is set **only when `DEBUG=False`**. Provide `RENDER_EXTERNAL_URL` in ENV.
- Behind proxies:
  ```python
  SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https')
  USE_X_FORWARDED_HOST=True
  ```

**Recommended commands on Render**  
- **Build**: `pip install -r requirements.txt && python manage.py collectstatic --noinput`  
- **Start**: `gunicorn cobien.wsgi --bind 0.0.0.0:$PORT`

---

## 10) How to **authorize your IP/domain** in MongoDB Atlas

For Atlas to accept connections, your **public IP** must be in the *Access List*:

1. Go to **MongoDB Atlas** → **Security** → **Network Access** → **Add IP Address**.
2. Options:
   - **Add current IP address** — great for local development.
   - **Allow access from anywhere (0.0.0.0/0)** *(temporary for testing)* — **not recommended** for production.
   - **Add a specific IP** — if you have a fixed server egress IP.
3. If you have a **domain** (e.g., `myapp.onrender.com`), note that Atlas accepts **IPs only**, **not domains**.  
   - Resolve the IP with `nslookup myapp.onrender.com` or `dig +short myapp.onrender.com` and add that IP.
   - Some PaaS (Render) can change egress IPs; in that case, consider “Allow from anywhere” temporarily or set up private networking/peering if available.
4. Save and wait a few seconds for the rule to apply.

**Test the connection**  
- From your machine/server, inspect the variable:
  ```bash
  echo $MONGO_URI   # (Linux/macOS)
  # or
  echo $Env:MONGO_URI  # (Windows PowerShell)
  ```
- Start Django and check logs. If it fails, typical messages:
  - `Authentication failed` → wrong credentials/user/DB.
  - `IP not allowed` → review the Access List.
  - Timeout → bad cluster/URI or network blocked.

---

## 11) Quick checklist

- [ ] `DJANGO_SECRET_KEY` set (prod).
- [ ] `DEBUG=False` and correct `ALLOWED_HOSTS` (prod).
- [ ] Correct `MONGO_URI` and `DB_NAME`.
- [ ] IP authorized in Atlas (*Network Access*).
- [ ] `RESEND_API_KEY` and `DEFAULT_FROM_EMAIL` set.
- [ ] **Twilio** credentials in ENV.
- [ ] **MQTT** broker configured.
- [ ] `collectstatic` run (prod) and **WhiteNoise** active.

---
## 12) Accounts
- Render -> usu: rendercobien@gmail.com  - Sc*Oo93LXM(dRxe4Ff$X&kQh
- MongoDb -> usu: mongodbcobien@gmail.com - Sc*Oo93LXM(dRxe4Ff$X&kQh
- Twilio -> usu: twiliocobien@gmail.com - Sc*Oo93LXM(dRxe4Ff$X&kQh 
  - (recovery code: UWDP5PAXWEK7KA5MEL626UZY)
	- SID Twilio	SK75b6011bed4f95a3950605b167324007
	- Scrt Twilio 	HX2mXk6GdHsIuHVgxpnAJPAzX6U70QQX

---

## 13) Final notes

- Avoid hard‑coding secrets. Use environment variables or a non‑versioned `.env`.
- If you rename apps or routes, review `INSTALLED_APPS`, `STATICFILES_DIRS`, `ROOT_URLCONF`, and `AUTHENTICATION_BACKENDS`.
- Email/username login uses `apps.accounts.backends.EmailOrUsernameModelBackend`.
