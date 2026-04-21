from datetime import datetime
from zoneinfo import ZoneInfo

from django import template

register = template.Library()

_TZ = ZoneInfo("Europe/Paris")

_MONTHS = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


@register.filter
def fecha_legible(value):
    """Return a human-readable date string like '21 de abril de 2026 a las 15:34'."""
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if not isinstance(value, datetime):
        return str(value)
    dt = value.astimezone(_TZ)
    return (
        f"{dt.day} de {_MONTHS[dt.month]} de {dt.year} "
        f"a las {dt.strftime('%H:%M')}"
    )
