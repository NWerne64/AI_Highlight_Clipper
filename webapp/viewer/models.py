from django.db import models
import os

# Hilfsfunktion, um Upload-Pfad zu definieren
def get_upload_path(instance, filename):
    # Speichert Datei in MEDIA_ROOT/uploads/<user_id>/<stream_link>/<filename>
    return os.path.join('uploads', str(instance.user_id), str(instance.stream_link), filename)

class Stream(models.Model):
    stream_link = models.CharField(max_length=200) # streamer page name (kann jetzt auch ein Titel sein)
    stream_name = models.CharField(max_length=200, blank=True, null=True) # stream name (optional machen?)
    user_id = models.CharField(max_length=200) # user id
    # NEUES FELD für die Videodatei
    video_file = models.FileField(upload_to=get_upload_path, blank=True, null=True) # Erlaubt leere Einträge erstmal

    def __str__(self):
         return f"{self.user_id} - {self.stream_link}"

class StreamHighlight(models.Model):
    user_id = models.CharField(max_length=200) # user id
    stream_link = models.CharField(max_length=200) # streamer page name
    clip_link = models.CharField(max_length=500) # link to stream clip (lokaler Pfad oder URL)
    # Verknüpfung zum ursprünglichen Stream-Objekt wäre besser
    # original_stream = models.ForeignKey(Stream, on_delete=models.CASCADE, null=True)

    def __str__(self):
         return f"Highlight for {self.stream_link} ({self.user_id})"