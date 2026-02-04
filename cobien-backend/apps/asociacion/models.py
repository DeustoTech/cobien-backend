from django.db import models
from django.contrib.auth.models import User

class Mensaje(models.Model):
    usuario = models.ForeignKey(User, on_delete=models.CASCADE)
    texto = models.TextField(blank=True, null=True)
    imagen = models.ImageField(upload_to="imagenes/", blank=True, null=True)
    video = models.FileField(upload_to="videos/", blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.usuario.username}: {self.texto[:30]}" if self.texto else f"{self.usuario.username} - Multimedia"
