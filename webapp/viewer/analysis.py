import os
import subprocess
import traceback
import numpy as np
import pandas as pd
import librosa
import cv2
from panns_inference import AudioTagging


class SoundDetector:
    def __init__(self, channel_name: str, stream_features: pd.DataFrame, stream_dataframe_name: str, video_path: str):
        self.channel_name = channel_name
        self.stream_dataframe_name = stream_dataframe_name
        self.stream_features = stream_features
        self.seconds = 10  # Analyseblock in Sekunden
        self.video_path = video_path

    def process(self, max_sound, labels_df, start_sec):
        i = len(self.stream_features)

        row = [start_sec, start_sec + self.seconds, float(max_sound)]
        for column in self.stream_features.columns[3:]:
            prediction = labels_df.loc[labels_df['display_name'] == column, 'prediction']
            row.append(float(prediction.iloc[0]) if not prediction.empty else 0.0)

        self.stream_features.loc[i] = row

        # 🔄 Speichere CSV im selben Verzeichnis wie das Video
        csv_path = os.path.join(os.path.dirname(self.video_path), f"{self.stream_dataframe_name}_sound.csv")
        self.stream_features.to_csv(csv_path, index=False)
        print(f"💾 CSV gespeichert: {csv_path}")

    def start(self):
        print("✅ Starte SoundDetector mit Sekunden-Zeitfenstern")
        print(f"🎬 Verwende Videopfad: {self.video_path}")

        # 📥 Temporäres Verzeichnis im selben Pfad wie Video
        temp_dir = os.path.join(os.path.dirname(self.video_path), "temp_audio")
        os.makedirs(temp_dir, exist_ok=True)

        # 📏 Videolänge ermitteln
        cap = cv2.VideoCapture(self.video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()

        if fps <= 0 or frame_count <= 0:
            print("❌ Fehler beim Öffnen des Videos oder keine Frames gefunden.")
            return

        duration = int(frame_count / fps)
        print(f"⏱️ Videolänge: {duration} Sekunden")

        for start_sec in range(0, duration, self.seconds):
            print(f"\n🔎 Analyse {start_sec}s bis {start_sec + self.seconds}s")

            try:
                temp_audio_path = os.path.join(temp_dir, f"{self.stream_dataframe_name}_{start_sec}.mp3")

                # 🎧 Audio extrahieren
                subprocess.call([
                    "ffmpeg",
                    "-ss", str(start_sec),
                    "-t", str(self.seconds),
                    "-i", self.video_path,
                    "-vn",
                    "-acodec", "libmp3lame",
                    temp_audio_path,
                    "-y"
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                # 🔊 Ton laden & Lautstärke bestimmen
                sound, _ = librosa.load(temp_audio_path, sr=32000, mono=True)
                max_sound = np.max(sound)

                # 🔍 Inferenz
                sound = sound[None, :]
                at = AudioTagging()
                clipwise_output, _ = at.inference(sound)

                # 📄 Labels + Prediction
                labels_path = os.path.join("webapp", "panns_data", "class_labels_indices.csv")
                labels_df = pd.read_csv(labels_path)
                labels_df['prediction'] = clipwise_output.reshape(-1)

                self.process(max_sound, labels_df, start_sec)

            except Exception as e:
                print(f"❌ Fehler bei Sekunde {start_sec} → {e}")
                traceback.print_exc()

        print("\n🏁 Analyse abgeschlossen.")
