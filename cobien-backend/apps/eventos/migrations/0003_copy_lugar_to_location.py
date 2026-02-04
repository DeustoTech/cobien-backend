from django.db import migrations

def copy_lugar_to_location(apps, schema_editor):
    Evento = apps.get_model('eventos', 'Evento')
    for ev in Evento.objects.all():
        if ev.lugar and not ev.location:
            ev.location = ev.lugar
            ev.save(update_fields=['location'])

class Migration(migrations.Migration):

    dependencies = [
        ('eventos', '0002_auto_20250710_1148'),
    ]

    operations = [
        migrations.RunPython(copy_lugar_to_location, migrations.RunPython.noop),
        migrations.RemoveField(           # ahora sí, una vez copiado
            model_name='evento',
            name='lugar',
        ),
    ]
