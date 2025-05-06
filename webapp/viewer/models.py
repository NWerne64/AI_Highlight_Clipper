# AI_Highlight_Clipper/webapp/viewer/models.py

from django.db import models
import os

def get_upload_path(instance, filename):
    user_id_part = str(instance.user_id) if instance.user_id else "unknown_user"
    stream_id_part = str(instance.id) if instance.id else "no_id_yet"
    return os.path.join('uploads', user_id_part, stream_id_part, filename)

class Stream(models.Model):
    user_id = models.CharField(max_length=200)
    stream_link = models.CharField(max_length=200)
    stream_name = models.CharField(max_length=200, blank=True, null=True)
    video_file = models.FileField(upload_to=get_upload_path, max_length=500, blank=True, null=True)

    # Analyse-Status ERWEITERT
    ANALYSIS_STATUS_CHOICES = [
        ('PENDING', 'Analyse Ausstehend'), # Für Uploads oder nach Aufnahme
        ('RECORDING_SCHEDULED', 'Aufnahme geplant'),
        ('RECORDING', 'Nimmt auf...'),
        ('PROCESSING', 'Analyse läuft...'),
        ('COMPLETE', 'Analyse Abgeschlossen'),
        ('ERROR', 'Fehler (Analyse/Aufnahme)'),
        ('OFFLINE', 'Offline (Keine Aufnahme)'),
        ('MANUALLY_STOPPED', 'Aufnahme Gestoppt'),
    ]
    analysis_status = models.CharField(max_length=25, choices=ANALYSIS_STATUS_CHOICES, default='PENDING')
    sound_csv_path = models.CharField(max_length=500, blank=True, null=True)
    avg_loudness = models.FloatField(null=True, blank=True)
    p90_loudness = models.FloatField(null=True, blank=True)
    p95_loudness = models.FloatField(null=True, blank=True)
    max_loudness = models.FloatField(null=True, blank=True)
    # --- Feld für Prozess-ID HINZUGEFÜGT ---
    recorder_pid = models.IntegerField(null=True, blank=True)
    # --- ENDE ---

    def get_analysis_status_display(self):
        return dict(Stream.ANALYSIS_STATUS_CHOICES).get(self.analysis_status, self.analysis_status)

    def __str__(self):
         return f"ID:{self.id} - {self.user_id} - {self.stream_name or self.stream_link} [{self.analysis_status}]" # Status hinzugefügt

class StreamHighlight(models.Model):
    user_id = models.CharField(max_length=200)
    stream_link = models.CharField(max_length=200)
    clip_link = models.CharField(max_length=500)

    def __str__(self):
         return f"Highlight for '{self.stream_link}' ({self.user_id}) - Clip: {self.clip_link}"