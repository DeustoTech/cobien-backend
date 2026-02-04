from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.models import User
from django import forms                              
from django.contrib.auth.forms import (
    UserCreationForm,
    AuthenticationForm,
)
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth import get_user_model
from django.conf import settings
from django.template.loader import render_to_string
import os
import requests

class SignUpForm(UserCreationForm):

    email = forms.EmailField(
        label=_("Correo electrónico"),
        required=True,
        help_text=_("Necesario para recuperar la cuenta."),
    )

    default_room = forms.CharField(
        label=_("ID de sala por defecto (opcional)"),
        max_length=100,
        required=False,
        help_text=_("Si ya conoces el nombre de tu sala, introdúcelo aquí."),
    )
    LANG_CHOICES = (
        ("es", _("Español")),
        ("fr", _("Français")),
    )
    preferred_language = forms.ChoiceField(
        choices=LANG_CHOICES,
        label=_("Idioma preferido"),
        initial="es",
        required=True,
        help_text=_("Selecciona el idioma por defecto para tu cuenta."),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "default_room", "preferred_language", "password1", "password2")

    def clean_username(self):
        username = self.cleaned_data["username"].lower()
        if User.objects.filter(username__iexact=username).count():
            raise forms.ValidationError("Ese nombre de usuario ya existe.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).count():
            raise forms.ValidationError("Ese correo ya está registrado.")
        return email

    def _post_clean(self):
        self.instance.validate_unique = lambda *args, **kwargs: None
        super()._post_clean()

class LoginForm(AuthenticationForm):
    error_messages = {
        "invalid_login": ("Usuario o contraseña incorrectos."),
        "inactive": ("Tu cuenta aún no está verificada. Revisa tu correo y actívala desde el email."),
    }

    def confirm_login_allowed(self, user):
        # Si el usuario existe pero está inactivo (no ha activado el email)
        if not user.is_active:
            raise forms.ValidationError(
                self.error_messages["inactive"],
                code="inactive",
            )

class EmailLoginForm(AuthenticationForm):
    # Sobrescribimos el campo username para que sea un EmailField
    username = forms.EmailField(
        label=_("Correo electrónico"),
        widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}),
    )

    error_messages = {
        "invalid_login": ("Correo o contraseña incorrectos."),
        "inactive": ("Tu cuenta aún no está verificada. Revisa tu correo y actívala."),
    }

    def confirm_login_allowed(self, user):
        if not user.is_active:
            raise forms.ValidationError(self.error_messages["inactive"], code="inactive")

class MongoFriendlyPasswordResetForm(PasswordResetForm):
    """
    Evita Djongo: no usa email__iexact ni .exists() y filtra en Python.
    Además, envía el correo de reset por la API HTTP de Resend, evitando smtplib.
    """

    def get_users(self, email):
        UserModel = get_user_model()
        if not email:
            return []
        raw = email.strip()
        users = list(UserModel._default_manager.filter(email=raw))
        if not users:
            lower_raw = raw.lower()
            if lower_raw != raw:
                users = list(UserModel._default_manager.filter(email=lower_raw))
        for user in users:
            if getattr(user, "is_active", True) and user.has_usable_password():
                yield user

    # --- Envío por API Resend (drop-in) ---
    def send_mail(
        self,
        subject_template_name,
        email_template_name,
        context,
        from_email,
        to_email,
        html_email_template_name=None,
    ):
        # 1) Toma la API key por este orden:
        #    a) settings.RESEND_API_KEY
        #    b) variable de entorno RESEND_API_KEY
        #    c) settings.EMAIL_HOST_PASSWORD (ya la usas con Resend SMTP)
        api_key = (
            getattr(settings, "RESEND_API_KEY", None)
            or os.getenv("RESEND_API_KEY")
            or getattr(settings, "EMAIL_HOST_PASSWORD", None)
        )

        # 2) Si en prod no hay API key, fallamos con mensaje claro.
        #    En local (DEBUG=True), hacemos fallback al envío estándar de Django (SMTP/console).
        if not api_key:
            from django.conf import settings as dj_settings
            if dj_settings.DEBUG:
                # Fallback local: usa el backend configurado (consola si lo pusiste, o SMTP si quieres)
                return super().send_mail(
                    subject_template_name,
                    email_template_name,
                    context,
                    from_email,
                    to_email,
                    html_email_template_name,
                )
            raise RuntimeError(
                "Falta la API key de Resend. Define RESEND_API_KEY en variables de entorno "
                "o en settings.RESEND_API_KEY / settings.EMAIL_HOST_PASSWORD."
            )

        subject = render_to_string(subject_template_name, context).strip()
        text_body = render_to_string(email_template_name, context)
        html_body = (
            render_to_string(html_email_template_name, context)
            if html_email_template_name
            else None
        )
        payload = {
            "from": from_email or settings.DEFAULT_FROM_EMAIL,
            "to": [to_email],
            "subject": subject,
            "html": html_body or f"<pre>{text_body}</pre>",
            "text": text_body,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        resp = requests.post(
            "https://api.resend.com/emails",
            json=payload,
            headers=headers,
            timeout=15,
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"Resend API error {resp.status_code}: {resp.text}")