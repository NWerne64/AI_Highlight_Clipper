# AI_Highlight_Clipper/webapp/viewer/models.py

from django.db import models
import os

def get_upload_path(instance, filename):
    safe_stream_link = "".join(c if c.isalnum() or c in ('_','-') else '_' for c in str(instance.stream_link))[:50]
    stream_id_part = str(instance.id) if instance.id else safe_stream_link
    user_id_part = str(instance.user_id) if instance.user_id else "unknown_user"
    # Speichere im Haupt-Media-Ordner, Unterordner für User und Stream-ID
    return os.path.join('uploads', user_id_part, str(stream_id_part) if instance.id else "temp", filename)

class Stream(models.Model):
    user_id = models.CharField(max_length=200) # Besser ForeignKey zum Django User Modell
    stream_link = models.CharField(max_length=200) # Titel
    stream_name = models.CharField(max_length=200, blank=True, null=True) # Optionaler Name
    video_file = models.FileField(upload_to=get_upload_path, blank=True, null=True)

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
    # Speichere den *relativen* Pfad zur CSV (relativ zu MEDIA_ROOT)
    sound_csv_path = models.CharField(max_length=500, blank=True, null=True)
    avg_loudness = models.FloatField(null=True, blank=True)
    p90_loudness = models.FloatField(null=True, blank=True)
    max_loudness = models.FloatField(null=True, blank=True)
    # --- NEUES FELD ---
    p95_loudness = models.FloatField(null=True, blank=True) # 95. Perzentil Lautstärke (RMS)
    # --- ENDE NEUES FELD ---

    # Methode um den Choice-Displaywert zu bekommen (für Templates)
    def get_analysis_status_display(self):
        return dict(Stream.ANALYSIS_STATUS_CHOICES).get(self.analysis_status, 'Unbekannt')

    def __str__(self):
         return f"ID:{self.id} - {self.user_id} - {self.stream_name or self.stream_link}"

class StreamHighlight(models.Model):
    # Optional: ForeignKey zu Stream (empfohlen!)
    # stream = models.ForeignKey(Stream, on_delete=models.CASCADE, related_name='highlights', null=True, blank=True)
    user_id = models.CharField(max_length=200)
    stream_link = models.CharField(max_length=200) # Zur Zuordnung
    # Speichert den relativen Pfad zum Clip (innerhalb von MEDIA_ROOT)
    clip_link = models.CharField(max_length=500)

    def __str__(self):
         return f"Highlight for '{self.stream_link}' ({self.user_id}) - Clip: {self.clip_link}"