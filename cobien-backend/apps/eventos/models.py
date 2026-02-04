from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

class Evento(models.Model):
    titulo = models.CharField(max_length=255)
    descripcion = models.TextField()
    fecha = models.DateTimeField()
    location = models.CharField(max_length=100, null=True, blank=True)  
    created_by = models.ForeignKey(User, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="eventos_creados")

    class Meta:
        db_table = 'eventos'

    def __str__(self):
        return f"{self.titulo} ({self.location})"
