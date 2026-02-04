# emociones/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('seleccionar/', views.seleccionar_rostro_mayor, name='seleccionar_rostro_mayor'),
    path('detectar/', views.detectar_emocion_superpuestos, name='detectar_emocion_superpuestos'),
    path('finalizar/', views.finalizar_emocion_sesion, name='finalizar_emocion_sesion'),
]
