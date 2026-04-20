from django.urls import path
from . import views
from django.conf.urls.i18n import set_language

urlpatterns = [
    path('', views.pizarra_home, name='pizarra_home'),
    path('icso/', views.icso_dashboard, name='pizarra_icso_dashboard'),
    path('devices/', views.devices_admin, name='pizarra_devices_admin'),
    path('devices/contacts/', views.device_contacts_admin, name='pizarra_device_contacts_admin'),
    path('nuevo/', views.pizarra_create, name='pizarra_create'),
    path('delete/<str:post_id>/', views.pizarra_delete, name='pizarra_delete'),
    path('img/<str:file_id>/', views.pizarra_image, name='pizarra_image'),
    path('api/messages/', views.api_pizarra_messages, name='pizarra_api_messages'),
    path('api/messages/<str:post_id>/delete/', views.api_delete_pizarra_message, name='pizarra_api_delete_message'),
    path('api/contacts/', views.api_contacts_for_device, name='pizarra_api_contacts'),
    path('api/contacts/sync/', views.api_trigger_contacts_sync, name='pizarra_api_contacts_sync'),
    path('api/mqtt/diagnostic/', views.api_mqtt_diagnostic, name='pizarra_api_mqtt_diagnostic'),
    path('api/devices/heartbeat/', views.api_device_heartbeat, name='pizarra_api_device_heartbeat'),
    path('api/icso/telemetry/', views.api_icso_telemetry, name='pizarra_api_icso_telemetry'),
    path('api/icso/events/', views.api_icso_events, name='pizarra_api_icso_events'),

    # Notificaciones
    path('api/notify/', views.api_notify, name='pizarra_api_notify'),  # endpoint para el mueble
    path('api/notifications/', views.api_notifications, name='pizarra_api_notifications'),  # opcional JSON para web
    path('notifications/mark-read/<str:notif_id>/', views.notification_mark_read, name='pizarra_notif_mark_read'),
    path('notifications/mark-all/', views.notification_mark_all, name='pizarra_notif_mark_all'),

    path('i18n/setlang/', set_language, name='set_language'),

]
