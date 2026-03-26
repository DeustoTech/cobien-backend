from django.urls import reverse_lazy
from django.views.generic import CreateView
from django.contrib.auth.views import LoginView, LogoutView
from .forms import SignUpForm, EmailLoginForm
import os
from pymongo import MongoClient
from django.contrib import messages
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth import get_user_model
from django.views import View
from django.shortcuts import redirect
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse
from django.utils.translation import activate, override, gettext as _

#  Mongo 
_client = MongoClient(os.getenv("MONGO_URI"))
db = _client["LabasAppDB"]

class SignUpView(CreateView):
    form_class = SignUpForm
    template_name = "registration/signup.html"
    success_url = reverse_lazy("login")

    def form_valid(self, form):
        # Crea el usuario
        response = super().form_valid(form)
        user = self.object

        # 1) Desactivar hasta confirmar
        user.is_active = False
        user.save(update_fields=["is_active"])

        # 2) Datos adicionales del formulario
        lang = form.cleaned_data.get("preferred_language") or "es"
        default_room = form.cleaned_data.get("default_room")

        # 3) Guardar en Mongo antes de enviar el email
        db["auth_user"].update_one(
            {"username": user.username},
            {"$set": {
                "default_room": default_room or None,
                "email_verified": False,
                "preferred_language": lang,
            }},
            upsert=True,
        )

        # 4) Enviar email en el idioma elegido
        enviar_email_activacion(self.request, user, lang)

        # 5) Mensaje y dejar la sesión ya en ese idioma
        with override(lang):
            messages.info(self.request, _("Te hemos enviado un email para activar tu cuenta."))

        activate(lang)
        self.request.session['django_language'] = lang
        response.set_cookie(
            getattr(settings, "LANGUAGE_COOKIE_NAME", "django_language"),
            lang,
            max_age=getattr(settings, "LANGUAGE_COOKIE_AGE", None),
            samesite=getattr(settings, "LANGUAGE_COOKIE_SAMESITE", "Lax"),
            secure=getattr(settings, "LANGUAGE_COOKIE_SECURE", False),
            domain=getattr(settings, "LANGUAGE_COOKIE_DOMAIN", None),
        )

        return response



class CustomLoginView(LoginView):
    template_name = "registration/login.html"
    form_class = EmailLoginForm
    redirect_authenticated_user = True   

    def form_valid(self, form):
        # primero deja que Django autentique y prepare la respuesta de redirección
        response = super().form_valid(form)

        user = self.request.user
        lang = "es"
        try:
            doc = db["auth_user"].find_one({"username": user.username}, {"preferred_language": 1})
            if doc and doc.get("preferred_language") in {"es", "fr"}:
                lang = doc["preferred_language"]
        except Exception:
            pass

        # Activa inmediatamente el idioma del usuario en la sesión
        activate(lang)
        self.request.session['django_language'] = lang

        # Persistir también en cookie
        response.set_cookie(
            getattr(settings, "LANGUAGE_COOKIE_NAME", "django_language"),
            lang,
            max_age=getattr(settings, "LANGUAGE_COOKIE_AGE", None),
            samesite=getattr(settings, "LANGUAGE_COOKIE_SAMESITE", "Lax"),
            secure=getattr(settings, "LANGUAGE_COOKIE_SECURE", False),
            domain=getattr(settings, "LANGUAGE_COOKIE_DOMAIN", None),
        )
        return response


class CustomLogoutView(LogoutView):
    pass

class ActivateAccountView(View):
    def get(self, request, uidb64, token):
        UserModel = get_user_model()
        try:
            uid = urlsafe_base64_decode(uidb64)
            user = UserModel.objects.get(pk=force_str(uid))
        except (TypeError, ValueError, OverflowError, UserModel.DoesNotExist):
            user = None

        if user is not None and default_token_generator.check_token(user, token):
            user.is_active = True
            user.save(update_fields=["is_active"])

            # Actualizar Mongo
            db["auth_user"].update_one(
                {"username": user.username},
                {"$set": {"email_verified": True}},
                upsert=True,
            )

            # Fijar idioma preferido del usuario en la sesión + cookie
            lang = "es"
            try:
                doc = db["auth_user"].find_one({"username": user.username}, {"preferred_language": 1})
                if doc and doc.get("preferred_language") in {"es", "fr"}:
                    lang = doc["preferred_language"]
            except Exception:
                pass

            activate(lang)
            request.session['django_language'] = lang
            response = redirect("login")
            response.set_cookie(
                getattr(settings, "LANGUAGE_COOKIE_NAME", "django_language"),
                lang,
                max_age=getattr(settings, "LANGUAGE_COOKIE_AGE", None),
                samesite=getattr(settings, "LANGUAGE_COOKIE_SAMESITE", "Lax"),
                secure=getattr(settings, "LANGUAGE_COOKIE_SECURE", False),
                domain=getattr(settings, "LANGUAGE_COOKIE_DOMAIN", None),
            )

            # Mensaje de éxito también en el idioma correcto
            with override(lang):
                messages.success(request, _("¡Tu cuenta ha sido activada! Ya puedes iniciar sesión."))

            return response

def enviar_email_activacion(request, user, lang="es"):
    # Construir enlace de activación
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    activation_url = request.build_absolute_uri(
        reverse("activate", kwargs={"uidb64": uid, "token": token})
    )

    ctx = {
        "username": user.username,
        "activation_url": activation_url,
        "site_name": "CoBien",
    }

    # Renderizar en el idioma elegido
    with override(lang):
        subject = _("Activa tu cuenta en CoBien")
        text_body = render_to_string("registration/activation_email.txt", ctx)
        html_body = render_to_string("registration/activation_email.html", ctx)

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send()
