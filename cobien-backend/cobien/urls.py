import os
from django.contrib import admin
from django.urls import path, include
from apps.eventos.views import (
    lista_eventos,
    home,
    app2,
    guardar_evento,
    extraer_evento,
    generate_video_token,
    videocall,
    videocall_device,
    device_videocall_session,
    toggle_emotion_detection,
)
from django.views.i18n import set_language
from django.conf import settings
from django.conf.urls.static import static
from apps.accounts.views import SignUpView, CustomLoginView, CustomLogoutView, ActivateAccountView

ENABLE_EMOCIONES = os.getenv("COBIEN_ENABLE_EMOCIONES", "1").strip().lower() not in {"0", "false", "no", "off"}
ENABLE_ASOCIACION = os.getenv("COBIEN_ENABLE_ASOCIACION", "1").strip().lower() not in {"0", "false", "no", "off"}

urlpatterns = [
    path('admin/', admin.site.urls),
    path('i18n/setlang/', set_language, name='set_language'),
    path('api/', include('apps.eventos.urls')),
    path('eventos/', lista_eventos, name='lista_eventos'),
    path('app2/', app2, name='tiempo'),
    path('api/guardar_evento/', guardar_evento, name='guardar_evento'),
    path('', home, name='home'),
    path('api/extraer_evento/', extraer_evento, name='extraer_evento'),
    path('api/generate-token/<str:identity>/<str:room_name>/', generate_video_token, name='generate_video_token'),
    path('api/device-videocall-session/', device_videocall_session, name='device_videocall_session'),
    path('videocall/', videocall, name='videocall'),
    path('videocall/device/', videocall_device, name='videocall_device'),
    path('api/emotion-toggle/', toggle_emotion_detection, name='toggle_emotion'),
    path('accounts/signup/',  SignUpView.as_view(),      name='signup'),
    path('accounts/login/',   CustomLoginView.as_view(), name='login'),
    path('accounts/logout/',  CustomLogoutView.as_view(),name='logout'),
    path("activar/<uidb64>/<token>/", ActivateAccountView.as_view(), name="activate"),
    path('accounts/', include('apps.accounts.urls')),
    path('pizarra/', include('apps.pizarra.urls')),

] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if ENABLE_EMOCIONES:
    urlpatterns.append(path('api/emociones/', include('apps.emociones.urls')))

if ENABLE_ASOCIACION:
    urlpatterns.append(path('asociacion/', include('apps.asociacion.urls')))
 
