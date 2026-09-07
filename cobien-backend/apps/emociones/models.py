from django.db import models

class EmocionDiaria(models.Model):
    dispositivo = models.CharField(max_length=100)
    fecha_hora = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=50)
    periodo = models.CharField(max_length=30, blank=True, default='')
    statements = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['-fecha_hora']

    def __str__(self):
        return f"{self.dispositivo} - {self.estado} ({self.fecha_hora.strftime('%d/%m/%Y %H:%M')})"
