# AI_Highlight_Clipper/webapp/viewer/models.py

from django.db import models
from django.utils import timezone  # Import für das Standard-Datum hinzufügen
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

    ANALYSIS_STATUS_CHOICES = [
        ('PENDING', 'Analyse Ausstehend'),
        ('RECORDING_SCHEDULED', 'Aufnahme geplant'),
        ('RECORDING', 'Nimmt auf...'),
        ('DOWNLOAD_SCHEDULED', 'VOD Download geplant'),
        ('DOWNLOADING', 'VOD lädt herunter...'),
        ('DOWNLOAD_COMPLETE', 'VOD Download Abgeschlossen'),
        ('PROCESSING', 'Analyse läuft...'),
        ('COMPLETE', 'Analyse Abgeschlossen'),
        ('ERROR', 'Fehler (Analyse/Aufnahme/Download)'),
        ('ERROR_DOWNLOAD', 'Fehler beim VOD Download'),
        ('ERROR_DOWNLOAD_TIMEOUT', 'VOD Download Timeout'),
        ('ERROR_NO_FILE', 'Fehler (Videodatei nicht gefunden)'),
        ('ERROR_STOP_FAILED', 'Fehler (Aufnahme konnte nicht gestoppt werden)'),
        ('OFFLINE', 'Offline (Keine Aufnahme)'),
        ('MANUALLY_STOPPED', 'Aufnahme Gestoppt'),
    ]
    analysis_status = models.CharField(max_length=30, choices=ANALYSIS_STATUS_CHOICES, default='PENDING')
    sound_csv_path = models.CharField(max_length=500, blank=True, null=True)
    avg_loudness = models.FloatField(null=True, blank=True)
    p90_loudness = models.FloatField(null=True, blank=True)
    p95_loudness = models.FloatField(null=True, blank=True)
    max_loudness = models.FloatField(null=True, blank=True)
    recorder_pid = models.IntegerField(null=True, blank=True)
    twitch_vod_id = models.CharField(max_length=50, blank=True, null=True, unique=True)
    chat_log_file = models.CharField(max_length=500, blank=True, null=True)

    # ### START DER NEUEN FELDER ###
    duration_seconds = models.IntegerField(null=True, blank=True, help_text="Dauer des Videos in Sekunden")
    created_at = models.DateTimeField(default=timezone.now, help_text="Erstellungs- oder Importdatum des Streams")
    # ### ENDE DER NEUEN FELDER ###

    def get_analysis_status_display(self):
        return dict(Stream.ANALYSIS_STATUS_CHOICES).get(self.analysis_status, self.analysis_status)

    def __str__(self):
         return f"ID:{self.id} - {self.user_id} - {self.stream_name or self.stream_link} [{self.analysis_status}] PID:[{self.recorder_pid or 'N/A'}] VOD:[{self.twitch_vod_id or 'N/A'}] ChatLog:[{self.chat_log_file or 'N/A'}]"

class StreamHighlight(models.Model):
    user_id = models.CharField(max_length=200)
    stream_link = models.CharField(max_length=200)
    clip_link = models.CharField(max_length=500)
    start_time = models.FloatField(null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
         return f"Highlight for '{self.stream_link}' (User: {self.user_id}) - Clip: {self.clip_link} (Start: {self.start_time or 'N/A'}s)"