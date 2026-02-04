# README — `settings.py` del proyecto CoBien

Este documento explica **qué hace** y **cómo configurar** el archivo `cobien/settings.py` del proyecto. Incluye:
- Mapa rápido de secciones del fichero y para qué sirven.
- Variables de entorno y valores por defecto.
- Configuración de **MongoDB** (djongo) y cómo conectarte en **local** (incluye comando para PowerShell).
- Email transaccional (Resend), **Twilio Video**, **MQTT**, estáticos/WhiteNoise, i18n (ES/FR) y seguridad para despliegue en **Render**.
- Plantilla para **pegar cuentas/contraseñas** por entorno (¡no la subas al repo!).
- Guía para **autorizar la IP** en MongoDB Atlas.

> **Ruta del archivo**: `backend-web/cobien-backend/cobien/settings.py`.

---

## 1) Mapa de `settings.py` (qué hace cada bloque)

- **Variables base**: `BASE_DIR`, `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`.
- **INSTALLED_APPS**: apps del proyecto (`apps.eventos`, `apps.asociacion`, `apps.emociones`, `apps.accounts`, `apps.pizarra`) + core Django + `rest_framework`.
- **MIDDLEWARE**: incluye `WhiteNoise` para servir estáticos en producción.
- **TEMPLATES** y `ROOT_URLCONF`, `WSGI_APPLICATION`.
- **DATABASES**: usa **djongo** apuntando a **MongoDB Atlas** a través de `MONGO_URI` (y `DB_NAME`). 
- **Auth**: validadores de contraseña, `AUTHENTICATION_BACKENDS` (login por email/username), URLs de login/logout.
- **i18n**: ES/FR con `LocaleMiddleware`, `LOCALE_PATHS=locale/`.
- **Static & Media**: `STATIC_URL`, `STATICFILES_DIRS` (`apps/eventos/static`), `STATIC_ROOT`, `MEDIA_ROOT`, almacenamiento con `WhiteNoise`.
- **Email**: SMTP de **Resend** con `RESEND_API_KEY` y `DEFAULT_FROM_EMAIL`.
- **Twilio**: `TWILIO_ACCOUNT_SID`, `TWILIO_API_KEY`, `TWILIO_API_SECRET`, etc.
- **MQTT**: `MQTT_BROKER_URL`, `MQTT_TOPIC_*`.
- **Seguridad/Proxy**: `CSRF_TRUSTED_ORIGINS` (en producción), `SECURE_PROXY_SSL_HEADER`, `USE_X_FORWARDED_HOST`.

---

## 2) Variables de entorno (ENV) usadas

> Define estas variables en tu entorno (Render o `.env` local). Donde hay _fallback_ en el código, **reemplázalo** en producción.

### Núcleo Django
- `DJANGO_SECRET_KEY` — **Obligatoria en producción**.
- `DEBUG` — `"True"`/`"False"`. En Render: **False**.
- `ALLOWED_HOSTS` — lista separada por comas (ej: `example.com,.onrender.com`).
- `RENDER_EXTERNAL_URL` — URL pública de Render para CSRF (solo prod).

### Base de datos (MongoDB via djongo)
- `DB_NAME` — p. ej. `LabasAppDB` (default en código).
- `MONGO_URI` — cadena de Atlas (ver sección 4).

### Email (Resend)
- `RESEND_API_KEY` — clave API.
- `DEFAULT_FROM_EMAIL` — p. ej. `CoBien <no-reply@portal.co-bien.eu>`.

### Twilio (Videollamada)
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN` *(si lo usas)*
- `TWILIO_API_KEY`
- `TWILIO_API_SECRET`
- `TWILIO_CONVERSATION_SERVICE_SID` *(opcional)*

### MQTT
- `MQTT_BROKER_URL` — ej. `broker.hivemq.com`
- `MQTT_BROKER_PORT` — ej. `1883`
- `MQTT_USERNAME` / `MQTT_PASSWORD` *(si aplica)*
- `MQTT_TOPIC_GENERAL` — default `tarjeta`
- `MQTT_TOPIC_VIDEOCALL` — default `videollamada`

---

## 3) Plantilla segura de credenciales por entorno

> **No subas este bloque con datos reales al repositorio.** Guárdalo en un gestor seguro o en **Render → Environment**.  
> Puedes copiar este esquema y completarlo para **DEV / STAGING / PROD**.

```text
====================
ENTORNO: DESARROLLO
====================
Django
- DJANGO_SECRET_KEY: ********************************
- DEBUG: True
- ALLOWED_HOSTS: localhost,127.0.0.1

MongoDB Atlas
- DB_NAME: LabasAppDB
- MONGO_URI: mongodb+srv://<usuario>:<password>@<cluster>/?retryWrites=true&w=majority&appName=<AppName>

Resend (email)
- RESEND_API_KEY: ****************************************
- DEFAULT_FROM_EMAIL: CoBien <no-reply@portal.co-bien.eu>

Twilio
- TWILIO_ACCOUNT_SID: AC********************************
- TWILIO_API_KEY: SK********************************
- TWILIO_API_SECRET: ********************************
- TWILIO_AUTH_TOKEN: (si aplica)
- TWILIO_CONVERSATION_SERVICE_SID: (opcional)

MQTT
- MQTT_BROKER_URL: broker.hivemq.com
- MQTT_BROKER_PORT: 1883
- MQTT_USERNAME: (si aplica)
- MQTT_PASSWORD: (si aplica)
- MQTT_TOPIC_GENERAL: tarjeta
- MQTT_TOPIC_VIDEOCALL: videollamada


====================
ENTORNO: PRODUCCIÓN
====================
Django
- DJANGO_SECRET_KEY: ********************************
- DEBUG: False
- ALLOWED_HOSTS: <tu-dominio>.onrender.com,<dominio-custom>
- RENDER_EXTERNAL_URL: https://<tu-servicio>.onrender.com

MongoDB Atlas
- DB_NAME: LabasAppDB
- MONGO_URI: mongodb+srv://<usuario>:<password>@<cluster>/?retryWrites=true&w=majority&appName=<AppName>

Resend (email)
- RESEND_API_KEY: ****************************************
- DEFAULT_FROM_EMAIL: CoBien <no-reply@portal.co-bien.eu>

Twilio
- (mismas claves que arriba, pero de PROD)

MQTT
- (mismas variables, apuntando a broker de PROD si aplica)
```

> **Sugerencia**: mantén un archivo privado `env.template` con claves **enmascaradas** y un `.env` local **no versionado**.

---

## 4) Conexión a **MongoDB** (local y producción)

El proyecto usa **djongo** y un cliente Mongo definido así en `DATABASES['default']`:
```python
'ENGINE': 'djongo',
'NAME': os.getenv('DB_NAME', 'LabasAppDB'),
'CLIENT': {'host': os.getenv('MONGO_URI', '<cadena-por-defecto>')}
```

### 4.1 Conectar en **local** (Windows PowerShell)
Ejecuta este comando (ejemplo pedido):
```powershell
$env:MONGO_URI = 'mongodb+srv://usuarioCoBien:passwordCoBien@clustercobienevents.j8ev5.mongodb.net/?retryWrites=true&w=majority&appName=ClusterCoBienEvents'
```
Opcionalmente define también:
```powershell
$env:DB_NAME = 'LabasAppDB'
$env:DEBUG = 'True'
```

### 4.2 Conectar en **local** (macOS/Linux bash)
```bash
export MONGO_URI='mongodb+srv://usuarioCoBien:passwordCoBien@clustercobienevents.j8ev5.mongodb.net/?retryWrites=true&w=majority&appName=ClusterCoBienEvents'
export DB_NAME='LabasAppDB'
export DEBUG=True
```

> Tras definir las variables, arranca el server:
> ```bash
> python manage.py runserver
> ```

### 4.3 Autorización de IP en Atlas (imprescindible)
Si recibes `IP not allowed` o `unable to connect`, sigue la **Sección 10**.

---

## 5) i18n (idiomas ES/FR)

- `LANGUAGE_CODE='es'`, `LANGUAGES=[('es','Español'),('fr','Français')]`.
- Asegúrate de tener `LocaleMiddleware` en `MIDDLEWARE` y la carpeta `locale/`.
- Comandos:
  ```bash
  django-admin makemessages -l es -l fr
  django-admin compilemessages
  ```

> **Zona horaria**: en `settings.py` está `TIME_ZONE='UTC'`. Si necesitas horario local en vistas/plantillas, ajusta `TIME_ZONE` o convierte en la capa de presentación.

---

## 6) Archivos estáticos y media

- En producción se usa **WhiteNoise**: 
  ```python
  MIDDLEWARE: 'whitenoise.middleware.WhiteNoiseMiddleware'
  STATICFILES_STORAGE='whitenoise.storage.CompressedManifestStaticFilesStorage'
  ```
- Directorios:
  - `STATICFILES_DIRS = ['apps/eventos/static']`
  - `STATIC_ROOT = 'staticfiles'`
  - `MEDIA_ROOT = 'media'`
- Recuerda ejecutar en prod:
  ```bash
  python manage.py collectstatic --noinput
  ```

---

## 7) Email (Resend) y Twilio

### Email con Resend
```python
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.resend.com"
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = "resend"
EMAIL_HOST_PASSWORD = os.getenv("RESEND_API_KEY")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "CoBien <no-reply@portal.co-bien.eu>")
```
- En local, si quieres ver emails en consola:
  ```python
  EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
  ```

### Twilio Video
Variables leídas desde ENV: `TWILIO_ACCOUNT_SID`, `TWILIO_API_KEY`, `TWILIO_API_SECRET`, etc.  
**No** dejes valores por defecto en producción.

---

## 8) MQTT

Define broker, puerto y credenciales en ENV; los topics por defecto son `tarjeta` (general) y `videollamada` para llamadas.

```python
MQTT_BROKER_URL, MQTT_BROKER_PORT, MQTT_USERNAME, MQTT_PASSWORD
MQTT_TOPIC_GENERAL, MQTT_TOPIC_VIDEOCALL
```

---

## 9) Seguridad y despliegue (Render)

- `DEBUG=False` en producción.
- `ALLOWED_HOSTS` debe incluir tu dominio de Render y cualquier dominio custom.
- `CSRF_TRUSTED_ORIGINS` se establece **solo cuando `DEBUG=False`**. Pon `RENDER_EXTERNAL_URL` en ENV.
- Servidores detrás de proxy: 
  ```python
  SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https')
  USE_X_FORWARDED_HOST=True
  ```

**Comandos recomendados en Render**  
- **Build**: `pip install -r requirements.txt && python manage.py collectstatic --noinput`  
- **Start**: `gunicorn cobien.wsgi --bind 0.0.0.0:$PORT`

---

## 10) Cómo **autorizar tu IP/dominio** en MongoDB Atlas

Para que Atlas acepte la conexión, tu **IP pública** debe estar en la *Access List*:

1. Entra a **MongoDB Atlas** → **Security** → **Network Access** → **Add IP Address**.
2. Opciones:
   - **Add current IP address** (tu IP actual). Útil para desarrollo local.
   - **Allow access from anywhere (0.0.0.0/0)** *(temporal para pruebas)* — **no recomendado** en producción.
   - **Add a specific IP**: si tienes un servidor con IP fija.
3. Si tienes un **dominio** (ej. `miapp.onrender.com`), recuerda que Atlas **no acepta dominios**, **solo IPs**.  
   - Resuelve la IP usando `nslookup miapp.onrender.com` o `dig +short miapp.onrender.com` y añade esa IP.
   - Ten en cuenta que algunos PAAS (Render) pueden cambiar IP de salida; si es tu caso, considera “Allow from anywhere” temporalmente o configura redes privadas/peering si está disponible.
4. Guarda y espera unos segundos a que la regla se aplique.

**Prueba la conexión**  
- Desde tu máquina/servidor, inspecciona la variable:
  ```bash
  echo $MONGO_URI   # (Linux/macOS)
  # o
  echo $Env:MONGO_URI  # (Windows PowerShell)
  ```
- Inicia Django y revisa logs. Si falla, mensajes típicos:
  - `Authentication failed` → credenciales/usuario/DB incorrectos.
  - `IP not allowed` → revisa la Access List.
  - Timeout → cluster/URI mal escrito o red bloqueada.

---

## 11) Checklist rápido

- [ ] `DJANGO_SECRET_KEY` definido (prod).
- [ ] `DEBUG=False` y `ALLOWED_HOSTS` correcto (prod).
- [ ] `MONGO_URI` y `DB_NAME` correctos.
- [ ] IP autorizada en Atlas (*Network Access*).
- [ ] `RESEND_API_KEY` y `DEFAULT_FROM_EMAIL` definidos.
- [ ] Credenciales de **Twilio** en ENV.
- [ ] Broker **MQTT** configurado.
- [ ] `collectstatic` ejecutado (prod) y `WhiteNoise` activo.

---
## 12) Cuentas
- Render -> usu: rendercobien@gmail.com  - Sc*Oo93LXM(dRxe4Ff$X&kQh
- MongoDb -> usu: mongodbcobien@gmail.com - Sc*Oo93LXM(dRxe4Ff$X&kQh
- Twilio -> usu: twiliocobien@gmail.com - Sc*Oo93LXM(dRxe4Ff$X&kQh 
  - (recovery code: UWDP5PAXWEK7KA5MEL626UZY)
	- SID Twilio	SK75b6011bed4f95a3950605b167324007
	- Scrt Twilio 	HX2mXk6GdHsIuHVgxpnAJPAzX6U70QQX

---

## 13) Notas finales

- Evita hardcodear claves. Usa variables de entorno o un `.env` **no versionado**.
- Si cambias nombres de apps o rutas, revisa `INSTALLED_APPS`, `STATICFILES_DIRS`, `ROOT_URLCONF` y `AUTHENTICATION_BACKENDS`.
- Para el login por email/usuario se usa `apps.accounts.backends.EmailOrUsernameModelBackend`.
