# webapp/viewer/templatetags/tags.py

from django import template

register = template.Library()

@register.filter
def modulo(num, val):
    return num % val

# ### NEUEN FILTER HINZUFÜGEN ###
@register.filter
def format_duration(seconds):
    """Formatiert Sekunden in ein HH:MM:SS-Format."""
    if seconds is None or not isinstance(seconds, (int, float)):
        return "00:00:00"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"