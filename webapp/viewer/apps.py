# webapp/viewer/apps.py
from django.apps import AppConfig

class ViewerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField' # Oder AutoField
    name = 'webapp.viewer' # <-- WICHTIG: Muss dieser Pfad sein!