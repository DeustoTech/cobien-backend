# accounts — README (EN)

> Authentication/registration app with email + username support, email-based account activation, password reset (via Resend HTTP API), language preferences (ES/FR), and user metadata stored in MongoDB Atlas.

---

## 1) What it does

- User sign‑up with **required email**, **preferred language**, and an optional **default room**.
- **Account activation by email** (users remain inactive until they confirm).
- **Login with email** (and also with username if the custom auth backend is enabled).
- **Password reset** using the **Resend HTTP API** (no SMTP) with a local fallback when `DEBUG=True`.
- **Language preference** (es/fr) stored in Mongo and applied to **session + cookie** after login/activation.
- Stores extra metadata in **MongoDB** (collection `auth_user`): `preferred_language`, `default_room`, `email_verified`.

---

## 2) Relevant structure

```
accounts/
├─ backends.py                 # Auth backend: email or username
├─ forms.py                    # Forms: signup, email login, and password reset (Resend)
├─ urls.py                     # Password reset routes (Django auth views)
├─ views.py                    # Views: signup, login, logout, activation
└─ templates/registration/
   ├─ activation_email.html
   ├─ activation_email.txt
   ├─ login.html
   ├─ password_reset_*.html|.txt (all pages of the flow)
   └─ signup.html
```

---

## 3) Dependencies & environment variables

**Required**

- `MONGO_URI` → MongoDB Atlas connection string.
- `DEFAULT_FROM_EMAIL` → sender for transactional emails.

**Optional but recommended**

- `RESEND_API_KEY` → API key to send **reset** emails via the Resend HTTP API.
  - In production, if missing, sending will raise a clear error.
  - In local dev (`DEBUG=True`), if missing, the configured email backend is used (console/SMTP).

---

## 4) `settings.py` configuration

### 4.1 App, templates & i18n

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
    'DIRS': [BASE_DIR / 'templates'],  # make sure to include your project-level templates dir
    'APP_DIRS': True,
    'OPTIONS': {
        'context_processors': [
            'django.template.context_processors.request',
            'django.contrib.auth.context_processors.auth',
            'django.contrib.messages.context_processors.messages',
        ],
    },
}]

LANGUAGE_CODE = 'es'  # default
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
# Default sender
DEFAULT_FROM_EMAIL = 'no-reply@yourdomain.com'

# Password reset via Resend HTTP
RESEND_API_KEY = os.getenv('RESEND_API_KEY')

# Local fallback when there is no RESEND_API_KEY and DEBUG=True
# (e.g., print emails to console)
if DEBUG:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
```

### 4.3 Authentication (optional but recommended)

Backend that allows authenticating with **email or username**:

```python
AUTHENTICATION_BACKENDS = [
    'accounts.backends.EmailOrUsernameModelBackend',
    'django.contrib.auth.backends.ModelBackend',  # keep as fallback
]
```

### 4.4 MongoDB

```python
MONGO_URI = os.getenv('MONGO_URI')  # e.g., Atlas connection string
```

---

## 5) URLs

This app exposes **only** the *password reset* flow by default. Add the rest in your project `urls.py`:

```python
from django.urls import path, include
from accounts.views import (
    SignUpView, CustomLoginView, CustomLogoutView, ActivateAccountView
)

urlpatterns = [
    # Main auth
    path('signup/',  SignUpView.as_view(),  name='signup'),
    path('login/',   CustomLoginView.as_view(), name='login'),
    path('logout/',  CustomLogoutView.as_view(), name='logout'),

    # Email activation
    path('activate/<uidb64>/<token>/', ActivateAccountView.as_view(), name='activate'),

    # Password reset flow (includes 4 routes)
    path('accounts/', include('accounts.urls')),
]
```

> Important: the views rely on the URL **names** `login` (post‑signup redirect) and `activate` (activation link). Keep those names consistent.

---

## 6) Key views & logic

### 6.1 Sign up (`SignUpView`)
- Creates the user as **inactive** (`is_active=False`).
- Stores Mongo (`auth_user`) metadata: `default_room`, `preferred_language`, `email_verified=False`.
- Sends **activation email** in the chosen language (`activation_email.html|.txt`). 
- Shows a **message** (“We’ve sent you an email…”) translated with `override(lang)`.
- Sets the `lang` in **session + cookie** right after registration.

### 6.2 Login (`CustomLoginView`)
- Login form using **email**.
- Reads `preferred_language` from Mongo and applies it to **session + cookie** after authentication.

### 6.3 Logout (`CustomLogoutView`)
- Uses Django’s standard `LogoutView` (no extra changes).

### 6.4 Account activation (`ActivateAccountView`)
- Validates token and activates the user (`is_active=True`).
- Marks `email_verified=True` in Mongo and applies **preferred language** to session/cookie.
- Shows a **success** message (“Your account has been activated! …”).

### 6.5 Activation email sending (`enviar_email_activacion`)
- Builds a link using `uidb64` + `token` → `activate` view.
- Renders subject + body in **ES/FR** with `override(lang)`.
- Sends with `EmailMultiAlternatives` (uses the email backend set in settings).

---

## 7) Forms

- **`SignUpForm`**
  - Fields: `username`, unique `email`, optional `default_room`, `preferred_language` (ES/FR), `password1/2`.
  - Validates case‑insensitive uniqueness of `username` & `email`.

- **`EmailLoginForm`** (email login)
  - Replaces `username` with an `EmailField` and blocks access if `is_active=False`.

- **`LoginForm`** (alternative)
  - Traditional login (username/email depending on backend) with custom error messages.

- **`MongoFriendlyPasswordResetForm`** (password reset)
  - Finds users by email with case‑insensitive matching.
  - **Sends via Resend HTTP** (uses `RESEND_API_KEY`) with a local fallback when `DEBUG=True`.

---

## 8) Authentication backend

**`EmailOrUsernameModelBackend`**
- Allows authentication using **email** (detects `@`) or the **USERNAME_FIELD** (default `username`).
- Uses Django core helpers `user_can_authenticate` and `check_password`.

> If you enable it, ensure **email is unique** (the form already validates this) to avoid ambiguity.

---

## 9) Required templates

In `templates/registration/` you should have:
- `signup.html`, `login.html`.
- Activation: `activation_email.html` and `activation_email.txt`.
- Password reset (Django): `password_reset_form.html`, `password_reset_email.txt`, `password_reset_email.html`, `password_reset_subject.txt`, `password_reset_done.html`, `password_reset_confirm.html`, `password_reset_complete.html`.

> The reset template names are referenced in `accounts/urls.py`.

---

## 10) End‑to‑end flow

1. **Signup** → inactive user + activation email in ES/FR → “we’ve sent you an email…” message.
2. **Activation** → activates user + sets `email_verified=True` + applies language to session/cookie → redirect to **login** with “account activated” message.
3. **Login** → email authentication → applies preferred language.
4. **Password reset** → form asks for email → email is sent via **Resend** (or console locally).

---

## 11) Notes & best practices

- **Mongo must be reachable** during signup/activation/login (reads/writes to `auth_user`).
- In production, **set `RESEND_API_KEY`**; otherwise the reset flow will fail with an explicit error.
- Keep `LocaleMiddleware` enabled and configure `LANGUAGES` so ES/FR selection works.
- If you change URL paths or names, review:
  - `success_url = reverse_lazy('login')` in `SignUpView`.
  - `reverse('activate', …)` in the activation email.
- To avoid `TemplateDoesNotExist`, verify all templates live under `templates/registration/` and that `APP_DIRS=True` or `DIRS` points correctly.

---

## 12) Quick verification checklist

- [ ] `accounts` in `INSTALLED_APPS`.
- [ ] `LocaleMiddleware` and `LANGUAGES` (es/fr) in settings.
- [ ] `AUTHENTICATION_BACKENDS` includes `accounts.backends.EmailOrUsernameModelBackend` (if you want email/username login).
- [ ] `DEFAULT_FROM_EMAIL` and `RESEND_API_KEY` defined.
- [ ] Valid and reachable `MONGO_URI`.
- [ ] Project `urls.py` includes `signup`, `login`, `logout`, `activate`.
- [ ] All `registration/` templates present.

---

## 13) Future enhancements

- Endpoint to **change language** from the user profile.
- Enforce **email uniqueness at model level** if migrating to a `CustomUser`.
- Replace SMTP for activation email with the Resend HTTP API to standardize.

---

**For the next maintainer**: with this README you should be able to (1) provision variables, (2) connect Mongo, and (3) run the full signup/activation/login/reset flow without touching code.
