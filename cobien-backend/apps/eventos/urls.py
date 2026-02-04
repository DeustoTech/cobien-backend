from django.urls import path
from .views import EventoList
from . import views

urlpatterns = [
    path('', EventoList.as_view(), name='evento-list'),
    path('call-answered/', views.call_answered, name='call_answered'),
]
