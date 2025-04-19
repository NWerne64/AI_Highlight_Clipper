# In sound.py
import moviepy.editor as mp
import librosa
import numpy as np
import pandas as pd
import os
from datetime import timedelta # Wird evtl. nicht mehr gebraucht

class SoundDetectorSimplified: # Umbenannt zur Klarheit

    def __init__(self, stream_dataframe_name: str): # Nur noch ID nötig
        self.stream_dataframe_name = stream_dataframe_name
        # Nur noch diese Spalten
        self.output_columns = ['start_time', 'end_time', 'sound_loudness']
        self.stream_features = pd.DataFrame([], columns=self.output_columns)
        self.sample_rate = 32000 # Behalte 32k oder wähle andere (z.B. 22050)
        self.segment_seconds = 1 # Kleinere Segmente für Lautstärke okay? Oder 3s behalten?
        self.hop_seconds = 0.5 # Kürzere Schritte für feinere Auflösung

    def analyze_file(self, video_path):
        print(f"Starting SIMPLIFIED sound analysis (Loudness) for: {video_path}")
        temp_audio_path = f"{self.stream_dataframe_name}_temp_audio.wav"

        try:
            # 1. Audio extrahieren
            print("Extracting audio...")
            video_clip = mp.VideoFileClip(video_path)
            # Konvertiere zu Mono und setze Samplerate direkt bei Extraktion
            audio_clip = video_clip.audio
            audio_clip.write_audiofile(temp_audio_path, samplerate=self.sample_rate, nbytes=2, codec='pcm_s16le', ffmpeg_params=["-ac", "1"]) # -ac 1 für Mono
            video_clip.close()
            audio_clip.close()
            print(f"Audio extracted to {temp_audio_path}")

            # 2. Audio laden
            print("Loading audio...")
            (sound, sr) = librosa.load(temp_audio_path, sr=self.sample_rate, mono=True) # Sicherstellen, dass sr korrekt ist
            print(f"Audio loaded. Duration: {len(sound) / sr:.2f}s")

            # 3. Lautstärke pro Segment berechnen
            segment_samples = int(self.segment_seconds * sr)
            hop_samples = int(self.hop_seconds * sr)
            results = []

            print(f"Analyzing loudness in {self.segment_seconds}s segments (hop={self.hop_seconds}s)...")
            for i in range(0, len(sound) - segment_samples, hop_samples):
                segment = sound[i : i + segment_samples]
                current_time_sec = i / sr

                # Lautstärke (RMS)
                rms = librosa.feature.rms(y=segment)[0]
                # Vermeide Log von Null oder sehr kleinen Zahlen
                # loudness_db = librosa.amplitude_to_db(rms, ref=np.max)[0] # dB ist oft intuitiver, hier aber RMS Wert
                mean_rms = np.mean(rms)

                results.append({
                    'start_time': current_time_sec,
                    'end_time': current_time_sec + self.segment_seconds,
                    'sound_loudness': mean_rms
                })

            # 4. Ergebnisse in DataFrame umwandeln und speichern
            self.stream_features = pd.DataFrame(results, columns=self.output_columns)
            output_csv_path = f'{self.stream_dataframe_name}_sound.csv'
            self.stream_features.to_csv(output_csv_path, index=False)
            print(f"Loudness analysis results saved to {output_csv_path}")

            return output_csv_path # Gib Pfad zur Ergebnisdatei zurück

        except Exception as e:
            print(f"ERROR during simplified sound analysis: {e}")
            import traceback
            traceback.print_exc()
            return None # Signalisiere Fehler
        finally:
            # 5. Temporäre Audiodatei löschen
            if os.path.exists(temp_audio_path):
                try:
                    os.remove(temp_audio_path)
                    print(f"Temporary audio file deleted: {temp_audio_path}")
                except Exception as e_del:
                    print(f"Warning: Could not delete temporary audio file {temp_audio_path}: {e_del}")