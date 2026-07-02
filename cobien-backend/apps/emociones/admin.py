from django.contrib import admin
from .models import EmocionDiaria

@admin.register(EmocionDiaria)
class EmocionDiariaAdmin(admin.ModelAdmin):
    list_display = ('dispositivo', 'estado', 'fecha_hora')
    list_filter = ('dispositivo', 'estado', 'fecha_hora')
    search_fields = ('dispositivo', 'estado')

