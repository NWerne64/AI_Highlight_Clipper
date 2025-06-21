from django import forms
from .models import Stream

class StreamUploadForm(forms.ModelForm):
    class Meta:
        model = Stream
        # Felder, die im Formular angezeigt werden sollen
        fields = ['stream_link', 'video_file']
        # Optional: Labels ändern
        labels = {
            'stream_link': 'Kanalname',
            'video_file': 'Videodatei auswählen',
        }