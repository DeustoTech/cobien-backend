# accounts/urls.py
from django.urls import path, reverse_lazy
from django.contrib.auth import views as auth_views
from .forms import MongoFriendlyPasswordResetForm
from .views import ForceChangePasswordView, MongoFriendlyPasswordResetConfirmView


urlpatterns = [
    path('change-password/', ForceChangePasswordView.as_view(), name='change_password_forced'),
    path(
        'password-reset/',
        auth_views.PasswordResetView.as_view(
            form_class=MongoFriendlyPasswordResetForm,
            template_name='registration/password_reset_form.html',
            email_template_name='registration/password_reset_email.txt',  # <- texto
            html_email_template_name='registration/password_reset_email.html',  # <- HTML
            subject_template_name='registration/password_reset_subject.txt',
            success_url=reverse_lazy('password_reset_done'),
        ),
        name='password_reset'
    ),
    path(
        'password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='registration/password_reset_done.html'
        ),
        name='password_reset_done'
    ),
    path(
        'reset/<uidb64>/<token>/',
        MongoFriendlyPasswordResetConfirmView.as_view(
            template_name='registration/password_reset_confirm.html',
            success_url=reverse_lazy('password_reset_complete'),
        ),
        name='password_reset_confirm'
    ),
    path(
        'reset/done/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='registration/password_reset_complete.html'
        ),
        name='password_reset_complete'
    ),
]
