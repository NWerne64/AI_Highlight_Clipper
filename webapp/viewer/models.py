# AI_Highlight_Clipper/webapp/viewer/models.py

from django.db import models
import os

# Hilfsfunktion für Upload-Pfad bleibt gleich
def get_upload_path(instance, filename):
    safe_stream_link = "".join(c if c.isalnum() or c in ('_','-') else '_' for c in str(instance.stream_link))[:50]
    # Verwende ID wenn verfügbar, sonst Fallback (beim ersten Speichern ist ID noch nicht da)
    stream_id_part = str(instance.id) if instance.id else "temp"
    return os.path.join('uploads', str(instance.user_id), stream_id_part, filename)

class Stream(models.Model):
    user_id = models.CharField(max_length=200)
    stream_link = models.CharField(max_length=200) # Titel
    stream_name = models.CharField(max_length=200, blank=True, null=True) # Optionaler Name
    video_file = models.FileField(upload_to=get_upload_path, blank=True, null=True)

    # --- NEUE FELDER für Analyse-Ergebnisse ---
    analysis_status = models.CharField(
        max_length=20,
        choices=[('PENDING', 'Pending'), ('PROCESSING', 'Processing'), ('COMPLETE', 'Complete'), ('ERROR', 'Error')],
        default='PENDING'
    )
    sound_csv_path = models.CharField(max_length=500, blank=True, null=True) # Pfad zur Lautstärke-CSV
    avg_loudness = models.FloatField(null=True, blank=True) # Durchschnittliche Lautstärke (RMS)
    p90_loudness = models.FloatField(null=True, blank=True) # 90. Perzentil Lautstärke (RMS)
    max_loudness = models.FloatField(null=True, blank=True) # Maximal gefundene Lautstärke (RMS)
    # --- ENDE NEUE FELDER ---

    def __str__(self):
         return f"ID:{self.id} - {self.user_id} - {self.stream_name or self.stream_link}"

class StreamHighlight(models.Model):
    # Optional: Verknüpfung zum Stream-Objekt (empfohlen!)
    # original_stream = models.ForeignKey(Stream, on_delete=models.CASCADE, null=True, blank=True, related_name='highlights')
    user_id = models.CharField(max_length=200)
    stream_link = models.CharField(max_length=200) # Zur Zuordnung, wenn kein ForeignKey
    clip_link = models.CharField(max_length=500) # Relativer Pfad zum Clip

    def __str__(self):
         return f"Highlight for {self.stream_link} ({self.user_id}) - Clip: {self.clip_link}"