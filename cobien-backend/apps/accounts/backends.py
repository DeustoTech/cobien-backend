# accounts/backends.py
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model

User = get_user_model()

class EmailOrUsernameModelBackend(ModelBackend):
    """
    Permite autenticar con email o con username.
    Si se pasa un email, busca user por email (case-insensitive).
    """
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)

        if username is None or password is None:
            return None

        user = None
        try:
            if "@" in username:
                # login por email
                user = User.objects.get(email__iexact=username)
            else:
                # login por username (fallback)
                user = User.objects.get(**{User.USERNAME_FIELD: username})
        except User.DoesNotExist:
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
