from django.urls import path
from . import views
from django.conf.urls.i18n import set_language

urlpatterns = [
    path('', views.pizarra_home, name='pizarra_home'),
    path('nuevo/', views.pizarra_create, name='pizarra_create'),
    path('delete/<str:post_id>/', views.pizarra_delete, name='pizarra_delete'),
    path('img/<str:file_id>/', views.pizarra_image, name='pizarra_image'),
    path('api/messages/', views.api_pizarra_messages, name='pizarra_api_messages'),
    path('api/messages/<str:post_id>/delete/', views.api_delete_pizarra_message, name='pizarra_api_delete_message'),

    # Notificaciones
    path('api/notify/', views.api_notify, name='pizarra_api_notify'),  # endpoint para el mueble
    path('api/notifications/', views.api_notifications, name='pizarra_api_notifications'),  # opcional JSON para web
    path('notifications/mark-read/<str:notif_id>/', views.notification_mark_read, name='pizarra_notif_mark_read'),
    path('notifications/mark-all/', views.notification_mark_all, name='pizarra_notif_mark_all'),

    path('i18n/setlang/', set_language, name='set_language'),

]
