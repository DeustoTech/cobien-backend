from django.urls import path
from . import views
from django.conf.urls.i18n import set_language

urlpatterns = [
    path('', views.pizarra_home, name='pizarra_home'),
    path('icso/', views.icso_dashboard, name='pizarra_icso_dashboard'),
    path('icso/download/events/', views.icso_download_events, name='pizarra_icso_download_events'),
    path('icso/download/snapshot/', views.icso_download_snapshot, name='pizarra_icso_download_snapshot'),
    path('devices/', views.devices_admin, name='pizarra_devices_admin'),
    path('devices/delete/', views.device_delete, name='pizarra_device_delete'),
    path('devices/contacts/', views.device_contacts_admin, name='pizarra_device_contacts_admin'),
    path('profile/', views.my_profile, name='my_profile'),
    path('people/', views.directory_people_admin, name='pizarra_directory_people_admin'),
    path('contact-images/<str:filename>/', views.contact_image, name='pizarra_contact_image'),
    path('person-images/<str:filename>/', views.directory_person_image, name='pizarra_directory_person_image'),
    path('nuevo/', views.pizarra_create, name='pizarra_create'),
    path('send/', views.pizarra_send_multi, name='pizarra_send_multi'),
    path('delete/<str:post_id>/', views.pizarra_delete, name='pizarra_delete'),
    path('web/delete/<str:post_id>/', views.pizarra_web_delete, name='pizarra_web_delete'),
    path('web/messages/', views.pizarra_web_messages, name='pizarra_web_messages'),
    path('img/<str:file_id>/', views.pizarra_image, name='pizarra_image'),
    path('api/messages/', views.api_pizarra_messages, name='pizarra_api_messages'),
    path('api/messages/<str:post_id>/delete/', views.api_delete_pizarra_message, name='pizarra_api_delete_message'),
    path('api/contacts/', views.api_contacts_for_device, name='pizarra_api_contacts'),
    path('api/contacts/sync/', views.api_trigger_contacts_sync, name='pizarra_api_contacts_sync'),
    path('api/device/poll/', views.api_device_poll, name='pizarra_api_device_poll'),
    path('api/device/diagnostic/', views.api_device_delivery_diagnostic, name='pizarra_api_device_diagnostic'),
    path('api/device/logs/ingest/', views.api_device_logs_ingest, name='pizarra_api_device_logs_ingest'),
    path('api/admin/devices/', views.api_admin_devices_list, name='pizarra_api_admin_devices_list'),
    path('api/admin/devices/<str:device_id>/cobien-env/', views.api_admin_device_env, name='pizarra_api_admin_device_env'),
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
