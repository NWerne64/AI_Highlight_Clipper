# AI_Highlight_Clipper/webapp/viewer/models.py

from django.db import models
import os

# Hilfsfunktion für Upload-Pfad (nutzt jetzt ID, falls vorhanden, sonst Titel)
# Achtung: Dies kann dazu führen, dass die Datei verschoben wird, wenn das Objekt
# zum ersten Mal gespeichert wird (ohne ID) und dann nochmal (mit ID).
# Besser: Eine feste Struktur, die die ID nicht *im* Pfad hat oder UUIDs.
# Wir lassen es vorerst so, wie es war, aber fügen die ID hinzu, wenn verfügbar.
def get_upload_path(instance, filename):
    # Bereinige stream_link für den Pfad
    safe_stream_link = "".join(c if c.isalnum() or c in ('_','-') else '_' for c in str(instance.stream_link))[:50]
    # Verwende ID wenn verfügbar, um Eindeutigkeit zu erhöhen
    stream_id_part = str(instance.id) if instance.id else safe_stream_link # Fallback auf bereinigten Link/Titel
    user_id_part = str(instance.user_id) if instance.user_id else "unknown_user"
    return os.path.join('uploads', user_id_part, stream_id_part, filename)

class Stream(models.Model):
    user_id = models.CharField(max_length=200) # Besser ForeignKey zum Django User Modell
    stream_link = models.CharField(max_length=200) # Jetzt eher der Titel?
    stream_name = models.CharField(max_length=200, blank=True, null=True) # Optionaler Name
    video_file = models.FileField(upload_to=get_upload_path, blank=True, null=True)

    # --- NEUE FELDER für Analyse-Ergebnisse ---
    ANALYSIS_STATUS_CHOICES = [
        ('PENDING', 'Ausstehend'),
        ('PROCESSING', 'In Arbeit'),
        ('COMPLETE', 'Abgeschlossen'),
        ('ERROR', 'Fehler'),
    ]
    analysis_status = models.CharField(
        max_length=20,
        choices=ANALYSIS_STATUS_CHOICES,
        default='PENDING'
    )
    # Speichere den *relativen* Pfad zur CSV im MEDIA-Ordner
    sound_csv_path = models.CharField(max_length=500, blank=True, null=True)
    avg_loudness = models.FloatField(null=True, blank=True) # Durchschnittliche Lautstärke (RMS)
    p90_loudness = models.FloatField(null=True, blank=True) # 90. Perzentil Lautstärke (RMS)
    max_loudness = models.FloatField(null=True, blank=True) # Maximal gefundene Lautstärke (RMS)
    # --- ENDE NEUE FELDER ---

    def __str__(self):
         return f"ID:{self.id} - {self.user_id} - {self.stream_name or self.stream_link}"

class StreamHighlight(models.Model):
    # Optional: Verknüpfung zum Stream-Objekt (SEHR empfohlen!)
    # stream = models.ForeignKey(Stream, on_delete=models.CASCADE, related_name='highlights', null=True, blank=True)
    user_id = models.CharField(max_length=200)
    stream_link = models.CharField(max_length=200) # Zur Zuordnung, wenn kein ForeignKey
    # Speichert den relativen Pfad zum Clip (innerhalb von MEDIA_ROOT)
    clip_link = models.CharField(max_length=500)

    def __str__(self):
         # Zeige relativen Pfad an
         return f"Highlight for '{self.stream_link}' ({self.user_id}) - Clip: {self.clip_link}"