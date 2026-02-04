from django.urls import path
from .views import  chat_asociacion, generate_twilio_token

urlpatterns = [
    path("", chat_asociacion, name="asociacion_chat"),  # Página del chat
    path("token/", generate_twilio_token, name="generate_twilio_token"),
]
