# AI_Highlight_Clipper/webapp/viewer/models.py

from django.db import models
import os

def get_upload_path(instance, filename):
    user_id_part = str(instance.user_id) if instance.user_id else "unknown_user"
    # Stelle sicher, dass instance.id existiert, bevor es verwendet wird.
    # Für den initialen Upload könnte die ID noch nicht final sein, bis das Objekt gespeichert ist.
    # Dieser Pfad wird dynamisch generiert, wenn das FileField tatsächlich die Datei speichert.
    stream_id_part = str(instance.id) if instance.id else "no_id_yet" # Fallback für den Fall, dass ID noch nicht da ist
    return os.path.join('uploads', user_id_part, stream_id_part, filename)

class Stream(models.Model):
    user_id = models.CharField(max_length=200)
    stream_link = models.CharField(max_length=200) # Bei Twitch-Aufnahmen/Import: Kanalname (lowercase)
    stream_name = models.CharField(max_length=200, blank=True, null=True) # Titel des Streams/VODs
    video_file = models.FileField(upload_to=get_upload_path, max_length=500, blank=True, null=True) # Relativer Pfad von MEDIA_ROOT

    ANALYSIS_STATUS_CHOICES = [
        ('PENDING', 'Analyse Ausstehend'),
        ('RECORDING_SCHEDULED', 'Aufnahme geplant'),
        ('RECORDING', 'Nimmt auf...'),
        ('DOWNLOAD_SCHEDULED', 'VOD Download geplant'), # NEU
        ('DOWNLOADING', 'VOD lädt herunter...'),      # NEU
        ('DOWNLOAD_COMPLETE', 'VOD Download Abgeschlossen'), # NEU
        ('PROCESSING', 'Analyse läuft...'),
        ('COMPLETE', 'Analyse Abgeschlossen'),
        ('ERROR', 'Fehler (Analyse/Aufnahme/Download)'),
        ('ERROR_DOWNLOAD', 'Fehler beim VOD Download'),    # NEU spezifischer
        ('ERROR_DOWNLOAD_TIMEOUT', 'VOD Download Timeout'), # NEU spezifischer
        ('ERROR_NO_FILE', 'Fehler (Videodatei nicht gefunden)'),# NEU spezifischer
        ('ERROR_STOP_FAILED', 'Fehler (Aufnahme konnte nicht gestoppt werden)'), # NEU spezifischer
        ('OFFLINE', 'Offline (Keine Aufnahme)'), # Für den Recorder-Status
        ('MANUALLY_STOPPED', 'Aufnahme Gestoppt'),
    ]
    analysis_status = models.CharField(max_length=30, choices=ANALYSIS_STATUS_CHOICES, default='PENDING') # Max length erhöht
    sound_csv_path = models.CharField(max_length=500, blank=True, null=True) # Relativer Pfad von MEDIA_ROOT
    avg_loudness = models.FloatField(null=True, blank=True)
    p90_loudness = models.FloatField(null=True, blank=True)
    p95_loudness = models.FloatField(null=True, blank=True)
    max_loudness = models.FloatField(null=True, blank=True)
    recorder_pid = models.IntegerField(null=True, blank=True) # PID des Aufnahme- oder Download-Prozesses

    # NEUES FELD für die Twitch VOD ID
    twitch_vod_id = models.CharField(max_length=50, blank=True, null=True, unique=True) # Eindeutig, falls gesetzt

    def get_analysis_status_display(self):
        return dict(Stream.ANALYSIS_STATUS_CHOICES).get(self.analysis_status, self.analysis_status)

    def __str__(self):
         return f"ID:{self.id} - {self.user_id} - {self.stream_name or self.stream_link} [{self.analysis_status}] PID:[{self.recorder_pid or 'N/A'}] VOD:[{self.twitch_vod_id or 'N/A'}]"

class StreamHighlight(models.Model):
    user_id = models.CharField(max_length=200)
    # stream_link sollte mit Stream.stream_link übereinstimmen (also Twitch-Kanalname oder Upload-Name)
    stream_link = models.CharField(max_length=200)
    clip_link = models.CharField(max_length=500) # Relativer Pfad von MEDIA_ROOT
    # NEUES FELD für die Sortierung von Highlights
    start_time = models.FloatField(null=True, blank=True) # Startzeit des Clips in Sekunden vom Videoanfang

    def __str__(self):
         return f"Highlight for '{self.stream_link}' (User: {self.user_id}) - Clip: {self.clip_link} (Start: {self.start_time or 'N/A'}s)"