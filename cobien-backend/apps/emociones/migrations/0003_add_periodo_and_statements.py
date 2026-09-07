# Generated for Django 4.1.13 / MongoDB Djongo

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('emociones', '0002_alter_emociondiaria_id'),
    ]

    operations = [
        # djongo (MongoDB) is schema-less and does not support standard ALTER TABLE ADD COLUMN.
        # We use SeparateDatabaseAndState so Django's migration state is
        # updated without running DDL statements on MongoDB.
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AddField(
                    model_name='emociondiaria',
                    name='periodo',
                    field=models.CharField(blank=True, default='', max_length=30),
                ),
                migrations.AddField(
                    model_name='emociondiaria',
                    name='statements',
                    field=models.JSONField(blank=True, default=list),
                ),
            ],
        ),
    ]
