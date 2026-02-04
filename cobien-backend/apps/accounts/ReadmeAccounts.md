# accounts — README

> App de autenticación/registro con soporte de email + username, activación por correo, recuperación de contraseña (vía Resend), preferencias de idioma (ES/FR) y metadatos de usuario en MongoDB Atlas.

---

## 1) Qué resuelve

* Registro de usuarios con **email obligatorio**, **idioma preferido** y **sala por defecto** (opcional).
* **Activación de cuenta por email** (el usuario queda inactivo hasta confirmar).
* **Login con email** (y también con username si se habilita el backend custom).
* **Recuperación de contraseña** usando la **API HTTP de Resend** (sin SMTP) con fallback local en `DEBUG=True`.
* **Preferencia de idioma** (es/fr) persistida en Mongo y aplicada a **sesión + cookie** tras login/activación.
* Almacena metadatos en **MongoDB** (colección `auth_user`): `preferred_language`, `default_room`, `email_verified`.

---

## 2) Estructura relevante

```
accounts/
├─ backends.py                 # Backend de auth: email o username
├─ forms.py                    # Formularios: signup, login por email y password reset (Resend)
├─ urls.py                     # Rutas de password reset (Django auth views)
├─ views.py                    # Vistas: registro, login, logout, activación
└─ templates/registration/
   ├─ activation_email.html
   ├─ activation_email.txt
   ├─ login.html
   ├─ password_reset_*.html|.txt (todas las vistas del flujo)
   └─ signup.html
```

---

## 3) Dependencias y variables de entorno

**Requeridas**

* `MONGO_URI` → cadena de conexión a MongoDB Atlas.
* `DEFAULT_FROM_EMAIL` → remitente para emails transaccionales.

**Opcionales pero recomendadas**

* `RESEND_API_KEY` → API key para enviar los emails de **reset** por Resend HTTP.

  * En producción, si falta, el envío lanza un error claro.
  * En local (`DEBUG=True`) si falta, se usa el backend de email configurado (consola/SMTP).

---

## 4) Configuración en `settings.py`

### 4.1 App, templates e i18n

```python
INSTALLED_APPS = [
    # ...
    'accounts',
    'django.contrib.auth',
    'django.contrib.messages',
    # ...
]

TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'],  # asegúrate de incluir el directorio raíz de templates
    'APP_DIRS': True,
    'OPTIONS': {
        'context_processors': [
            'django.template.context_processors.request',
            'django.contrib.auth.context_processors.auth',
            'django.contrib.messages.context_processors.messages',
        ],
    },
}]

LANGUAGE_CODE = 'es'  # por defecto
USE_I18N = True
LANGUAGES = (
    ('es', 'Español'),
    ('fr', 'Français'),
)
LOCALE_PATHS = [BASE_DIR / 'locale']
MIDDLEWARE = [
    # ...
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    # ...
]
```

### 4.2 Email

```python
# Remitente por defecto
DEFAULT_FROM_EMAIL = 'no-reply@tudominio.com'

# Para el flujo de reset por Resend HTTP
RESEND_API_KEY = os.getenv('RESEND_API_KEY')

# Fallback local si no hay RESEND_API_KEY y DEBUG=True
# (p. ej. mostrar en consola)
if DEBUG:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
```

### 4.3 Autenticación (opcional pero recomendado)

Backend que permite autenticarse con **email o username**:

```python
AUTHENTICATION_BACKENDS = [
    'accounts.backends.EmailOrUsernameModelBackend',
    'django.contrib.auth.backends.ModelBackend',  # mantener como fallback
]
```

### 4.4 MongoDB

```python
MONGO_URI = os.getenv('MONGO_URI')  # p.ej. cadena de Atlas
```

---

## 5) URLs

Esta app ya expone **solo** el flujo de *password reset*. Añade en tu `urls.py` de proyecto:

```python
from django.urls import path, include
from accounts.views import (
    SignUpView, CustomLoginView, CustomLogoutView, ActivateAccountView
)

urlpatterns = [
    # Auth principal
    path('signup/',  SignUpView.as_view(),  name='signup'),
    path('login/',   CustomLoginView.as_view(), name='login'),
    path('logout/',  CustomLogoutView.as_view(), name='logout'),

    # Activación por email
    path('activate/<uidb64>/<token>/', ActivateAccountView.as_view(), name='activate'),

    # Flujo de recuperación de contraseña (incluye 4 rutas)
    path('accounts/', include('accounts.urls')),  
]
```

> Importante: las vistas usan los **names** `login` (post-registro) y `activate` (enlace del correo). Mantén esos names.

---

## 6) Vistas y lógica clave

### 6.1 Registro (`SignUpView`)

* Crea el usuario **inactivo** (`is_active=False`).
* Guarda en Mongo (`auth_user`) los metadatos `default_room`, `preferred_language`, `email_verified=False`.
* Envía **email de activación** en el idioma elegido (`activation_email.html|.txt`).
* Muestra un **mensaje** (“Te hemos enviado un email…”) traducido con `override(lang)`.
* Activa el `lang` en **sesión + cookie** inmediatamente tras registrarse.

### 6.2 Login (`CustomLoginView`)

* Formulario de login por **email**.
* Lee `preferred_language` de Mongo y lo aplica a **sesión + cookie** tras autenticar.

### 6.3 Logout (`CustomLogoutView`)

* Usa el `LogoutView` estándar (sin cambios adicionales).

### 6.4 Activación de cuenta (`ActivateAccountView`)

* Valida token y activa el usuario (`is_active=True`).
* Marca `email_verified=True` en Mongo y aplica el **idioma preferido** a sesión/cookie.
* Muestra **mensaje de éxito** traducido (“¡Tu cuenta ha sido activada! …”).

### 6.5 Envío del email de activación (`enviar_email_activacion`)

* Construye un enlace con `uidb64` + `token` → vista `activate`.
* Renderiza subject + cuerpo en **ES/FR** con `override(lang)`.
* Envío con `EmailMultiAlternatives` (usa el backend definido en settings).

---

## 7) Formularios

* **`SignUpForm`**

  * Campos: `username`, `email` (único), `default_room` (opcional), `preferred_language` (ES/FR), `password1/2`.
  * Valida unicidad *case-insensitive* de `username` & `email`.

* **`EmailLoginForm`** (login por email)

  * Reemplaza `username` por un `EmailField` y bloquea acceso si `is_active=False`.

* **`LoginForm`** (alternativo)

  * Login tradicional (username/email dependiendo del backend) con mensajes de error personalizados.

* **`MongoFriendlyPasswordResetForm`** (recuperación de contraseña)

  * Búsqueda de usuarios por email con tolerancia de mayúsculas/minúsculas.
  * **Envío por Resend HTTP** (usa `RESEND_API_KEY`) con fallback a backend local si `DEBUG=True`.

---

## 8) Backend de autenticación

**`EmailOrUsernameModelBackend`**

* Permite autenticarse con **email** (detecta `@`) o con el **USERNAME_FIELD** (por defecto `username`).
* Usa `user_can_authenticate` y `check_password` del core de Django.

> Si lo habilitas, asegúrate de que el **email sea único** (el formulario ya lo valida) para evitar ambigüedad.

---

## 9) Templates imprescindibles

En `templates/registration/` deben existir:

* `signup.html`, `login.html`.
* Activación: `activation_email.html` y `activation_email.txt`.
* Password reset (Django): `password_reset_form.html`, `password_reset_email.txt`, `password_reset_email.html`, `password_reset_subject.txt`, `password_reset_done.html`, `password_reset_confirm.html`, `password_reset_complete.html`.

> Los *names* de templates de reset ya están referenciados en `accounts/urls.py`.

---

## 10) Flujo funcional (end-to-end)

1. **Signup** → usuario inactivo + email de activación en ES/FR → mensaje “te hemos enviado un email…”.
2. **Activación** → activa usuario + marca `email_verified=True` + fija idioma en sesión/cookie → redirige a **login** con mensaje “cuenta activada”.
3. **Login** → autenticación por email → aplica idioma preferido.
4. **Password reset** → formulario pide email → correo se envía por **Resend** (o consola en local).

---

## 11) Notas y buenas prácticas

* **Mongo debe estar accesible** durante registro/activación/login (se consulta/actualiza `auth_user`).
* En producción, **define `RESEND_API_KEY`**; si falta, el flujo de reset fallará con error explícito.
* Mantén `LocaleMiddleware` y configura `LANGUAGES` para que la selección ES/FR funcione.
* Si cambias los paths o names de URLs, revisa:

  * `success_url = reverse_lazy('login')` en `SignUpView`.
  * `reverse('activate', …)` en el email de activación.
* Para evitar `TemplateDoesNotExist`, verifica que todos los templates estén en `templates/registration/` y que `APP_DIRS=True` o `DIRS` apunte correctamente.

---

## 12) Checklist de verificación rápida

* [ ] `accounts` en `INSTALLED_APPS`.
* [ ] `LocaleMiddleware` y `LANGUAGES` (es/fr) en settings.
* [ ] `AUTHENTICATION_BACKENDS` incluye `accounts.backends.EmailOrUsernameModelBackend` (si deseas login con email/usuario).
* [ ] `DEFAULT_FROM_EMAIL` y `RESEND_API_KEY` definidos.
* [ ] `MONGO_URI` válido y accesible.
* [ ] URLs de `signup`, `login`, `logout`, `activate` añadidas en el `urls.py` del proyecto.
* [ ] Templates de `registration/` presentes.

---

## 13) Extensión futura

* Añadir endpoint para **cambio de idioma** desde el perfil.
* Validar **unicidad de email a nivel de modelo** si se migra a un `CustomUser`.
* Sustituir envíos SMTP del email de activación por Resend HTTP para homogeneizar.

---

**Mantenedor/a siguiente**: con este README deberías poder:

1. Provisionar variables, 2) conectar Mongo y 3) ejecutar todo el flujo de alta/activación/login/reset sin tocar código.
