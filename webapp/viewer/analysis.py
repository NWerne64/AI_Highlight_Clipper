# AI_Highlight_Clipper/webapp/viewer/analysis.py

import moviepy.editor as mp
import librosa
import numpy as np
import pandas as pd
import os
import time
import subprocess
import traceback
import threading

try:
    from django.conf import settings # Für MEDIA_ROOT Zugriff
    from .models import Stream, StreamHighlight
    MODELS_AVAILABLE = True
except ImportError as e:
    print(f"FATAL ERROR in analysis.py: Cannot import Django components! ({e})")
    Stream = None
    StreamHighlight = None
    MODELS_AVAILABLE = False
    settings = None # Sicherstellen, dass settings nicht undefiniert ist

# --- Konfiguration ---
LOUDNESS_THRESHOLD = 0.08 # !!! Standard-Threshold für initiale Analyse & Re-Generierung !!!
CLIP_DURATION_S = 5
TARGET_SAMPLE_RATE = 22050
SEGMENT_SECONDS = 0.5
HOP_SECONDS = 0.2

# --- Vereinfachter Sound Detector ---
class SoundDetectorSimplified:
    def __init__(self, stream_dataframe_name: str):
        self.stream_dataframe_name = stream_dataframe_name
        self.output_columns = ['start_time', 'end_time', 'sound_loudness']
        self.stream_features = pd.DataFrame([], columns=self.output_columns)
        self.sample_rate = TARGET_SAMPLE_RATE
        self.segment_seconds = SEGMENT_SECONDS
        self.hop_seconds = HOP_SECONDS

    # Gibt jetzt CSV-Pfad und Statistiken zurück
    def analyze_file(self, video_path, stream_media_dir): # Nimmt Zielordner für CSV entgegen
        base_name = f"stream_{self.stream_dataframe_name}_loudness"
        # Temporäre Audio-Datei im Projektstamm/analysis_temp
        temp_dir = "analysis_temp"
        os.makedirs(temp_dir, exist_ok=True)
        temp_audio_path = os.path.join(temp_dir, f"{base_name}_temp_audio.wav")
        # CSV-Datei im spezifischen media-Ordner des Streams speichern
        output_csv_path = os.path.join(stream_media_dir, f"{base_name}_sound.csv")

        thread_id = threading.get_ident()
        log_prefix = f"[Thread-{thread_id} Analysis] "
        print(log_prefix + f"Starting sound analysis for: {video_path}")
        max_loudness_found = 0.0
        avg_loudness = 0.0
        p90_loudness = 0.0

        try:
            # 1. Audio extrahieren
            print(log_prefix + "Extracting audio...")
            t_start_extract = time.time()
            cmd_extract = [ 'ffmpeg', '-i', video_path, '-vn', '-acodec', 'pcm_s16le',
                            '-ar', str(self.sample_rate), '-ac', '1', '-loglevel', 'error', temp_audio_path, '-y' ]
            subprocess.run(cmd_extract, check=True, capture_output=True, text=True, encoding='utf-8')
            print(log_prefix + f"Audio extracted (took {time.time()-t_start_extract:.2f}s)")

            # 2. Audio laden
            print(log_prefix + "Loading audio...")
            t_start_load = time.time()
            (sound, sr) = librosa.load(temp_audio_path, sr=self.sample_rate, mono=True)
            print(log_prefix + f"Audio loaded (Duration: {librosa.get_duration(y=sound, sr=sr):.2f}s, took {time.time()-t_start_load:.2f}s)")

            # 3. Lautstärke berechnen
            segment_samples = int(self.segment_seconds * sr)
            hop_samples = int(self.hop_seconds * sr)
            results = []
            num_segments = max(0, (len(sound) - segment_samples) // hop_samples + 1)
            print(log_prefix + f"Analyzing loudness in approx. {num_segments} segments...")
            t_start_rms = time.time()
            all_rms_values = []

            for i in range(0, len(sound) - segment_samples + 1, hop_samples):
                segment = sound[i : i + segment_samples]
                current_time_sec = i / sr
                rms = librosa.feature.rms(y=segment)[0]
                mean_rms = np.mean(rms)
                all_rms_values.append(mean_rms)
                results.append({'start_time': current_time_sec, 'end_time': current_time_sec + self.segment_seconds, 'sound_loudness': mean_rms})

            print(log_prefix + f"Loudness analysis finished (took {time.time() - t_start_rms:.2f}s)")
            if not results:
                 print(log_prefix + "No loudness segments generated.")
                 return None, 0.0, 0.0, 0.0 # Gib Nullen zurück

            # 4. Ergebnisse & Statistiken berechnen/speichern
            self.stream_features = pd.DataFrame(results, columns=self.output_columns)
            self.stream_features.to_csv(output_csv_path, index=False)
            print(log_prefix + f"Loudness results saved to {output_csv_path}")

            loudness_series = pd.Series(all_rms_values)
            avg_loudness = loudness_series.mean()
            p90_loudness = loudness_series.quantile(0.90)
            max_loudness_found = loudness_series.max()
            print(log_prefix + f"Loudness Stats: Avg={avg_loudness:.4f}, 90th Percentile={p90_loudness:.4f}, Max={max_loudness_found:.4f}")

            return output_csv_path, avg_loudness, p90_loudness, max_loudness_found

        except FileNotFoundError:
             print(log_prefix + f"ERROR: ffmpeg command not found.")
             return None, 0.0, 0.0, 0.0
        except Exception as e:
            print(log_prefix + f"ERROR during simplified sound analysis: {e}")
            traceback.print_exc()
            return None, 0.0, 0.0, 0.0
        finally:
            if os.path.exists(temp_audio_path):
                try: os.remove(temp_audio_path); print(log_prefix + f"Deleted temp audio.")
                except Exception as e_del: print(log_prefix + f"Warning: Could not delete temp audio: {e_del}")

# --- Highlight-Logik ---
def find_highlights_by_loudness(sound_csv_path, video_path, stream_id, user_name, threshold):
    # ... (Funktion bleibt exakt wie im vorigen Beispiel, inkl. Löschen alter Highlights) ...
    thread_id = threading.get_ident()
    log_prefix = f"[Thread-{thread_id} HighlightFind] "

    if not MODELS_AVAILABLE:
         print(log_prefix + "ERROR: Django Models not available.")
         return []
    try:
        stream_obj = Stream.objects.get(id=stream_id)
    except Exception as e_db:
         print(log_prefix + f"ERROR accessing DB for Stream ID {stream_id}: {e_db}")
         return []

    print(log_prefix + f"Finding highlights (Threshold={threshold:.4f}, Clip={CLIP_DURATION_S}s) in {sound_csv_path}")
    try:
        df = pd.read_csv(sound_csv_path)
        if df.empty: return []

        loud_start_times = df[df['sound_loudness'] > threshold]['start_time'].tolist()
        if not loud_start_times:
            print(log_prefix + "No segments found above threshold.")
            # Wichtig: Alte Highlights wurden evtl. gelöscht, aber keine neuen gefunden
            return []

        print(log_prefix + f"Found {len(loud_start_times)} segments above threshold. Extracting {CLIP_DURATION_S}s clips...")

        # Alte Highlights löschen vor Neuerstellung
        old_highlights = StreamHighlight.objects.filter(user_id=user_name, stream_link=stream_obj.stream_link)
        if old_highlights.exists():
             print(log_prefix + f"  Deleting {old_highlights.count()} old highlight entries/files...")
             for hl in old_highlights:
                  try:
                       if hl.clip_link and settings: # Prüfe ob settings importiert wurde
                            clip_full_path = os.path.join(settings.MEDIA_ROOT, hl.clip_link)
                            if os.path.exists(clip_full_path): os.remove(clip_full_path)
                  except Exception as e_del_old: print(f"  Warning: Could not delete old clip file {hl.clip_link}: {e_del_old}")
             old_highlights.delete()

        extracted_clips_info = []
        output_dir = os.path.dirname(video_path)
        if not os.path.exists(output_dir): os.makedirs(output_dir, exist_ok=True)

        last_clip_end_time = -float('inf')
        clip_counter = 0

        for start_time_s in loud_start_times:
            if start_time_s < last_clip_end_time: continue

            clip_actual_start_s = max(0, start_time_s - 1.0)
            clip_start_time_str = time.strftime('%H:%M:%S', time.gmtime(clip_actual_start_s)) + '.' + str(int((clip_actual_start_s % 1) * 1000))
            clip_duration_str = time.strftime('%H:%M:%S', time.gmtime(CLIP_DURATION_S)) + '.000'
            clip_name = f"highlight_loud_{stream_obj.id}_{int(start_time_s)}.mp4"
            clip_output_path = os.path.join(output_dir, clip_name)

            print(log_prefix + f"  Extracting clip {clip_counter+1}: {clip_name} (Start: {start_time_s:.2f}s)")

            cmd = [ 'ffmpeg', '-ss', clip_start_time_str, '-i', video_path, '-t', clip_duration_str,
                    '-c', 'copy', '-map', '0', '-avoid_negative_ts', 'make_zero', '-loglevel', 'error',
                    clip_output_path, '-y' ]
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True, encoding='utf-8')
                print(log_prefix + f"  Clip extracted: {clip_output_path}")

                relative_clip_path = ""
                try: # Relativen Pfad sicher berechnen
                     if settings:
                          media_root = settings.MEDIA_ROOT
                          relative_clip_path = os.path.relpath(clip_output_path, media_root).replace('\\', '/')
                          if "../" in relative_clip_path: raise ValueError("Clip path outside media root")
                     else: raise ValueError("Settings not available")
                except Exception:
                     relative_clip_path = os.path.join('uploads', str(user_name), str(stream_id), clip_name).replace('\\','/')
                     print(log_prefix + f"  WARNUNG: Relativer Pfad geraten: {relative_clip_path}")

                if MODELS_AVAILABLE:
                    StreamHighlight.objects.create(
                        user_id=user_name,
                        stream_link=stream_obj.stream_link,
                        clip_link=relative_clip_path
                    )
                    print(log_prefix + f"  Highlight '{relative_clip_path}' saved to database.")
                    extracted_clips_info.append({'path': clip_output_path, 'relative': relative_clip_path})
                    last_clip_end_time = clip_actual_start_s + CLIP_DURATION_S
                    clip_counter += 1

            except subprocess.CalledProcessError as e:
                print(log_prefix + f"  ERROR extracting clip {clip_name}: {e}")
                if e.stderr: print(f"  FFmpeg stderr: {e.stderr.strip()}")
            except Exception as e_clip:
                 print(log_prefix + f"  ERROR during clip extraction or DB save for {clip_name}: {e_clip}")
                 traceback.print_exc()
        return extracted_clips_info

    except FileNotFoundError:
        print(log_prefix + f"ERROR: Sound CSV file not found at {sound_csv_path}")
        return []
    except Exception as e:
        print(log_prefix + f"ERROR during highlight finding in thread: {e}")
        traceback.print_exc()
        return []


# --- Hauptfunktion für den Thread ---
def run_analysis_and_extraction_thread(video_path, stream_id, user_name):
    thread_id = threading.get_ident()
    log_prefix = f"[Thread-{thread_id}] "
    print(f"\n{log_prefix}--- Thread started for Stream ID: {stream_id}, Video: {video_path} ---")
    start_time_thread = time.time()

    if not MODELS_AVAILABLE:
        print(f"{log_prefix}--- Thread stopped early: Django Models not available. ---")
        return

    analysis_run_id = f"{stream_id}"
    stream_obj = None
    relative_csv_path_for_db = None # Zum Speichern in DB

    try:
        # Status auf PROCESSING setzen
        # (Innerhalb des Threads, um DB-Zugriffe zu bündeln)
        stream_obj = Stream.objects.get(id=stream_id)
        stream_obj.analysis_status = 'PROCESSING'
        stream_obj.save(update_fields=['analysis_status'])

        # 1. Lautstärke-Analyse
        # Übergib den Zielordner für die CSV
        stream_media_dir = os.path.dirname(video_path)
        sound_detector = SoundDetectorSimplified(stream_dataframe_name=analysis_run_id)
        sound_csv_full_path, avg_loudness, p90_loudness, max_loudness = sound_detector.analyze_file(video_path, stream_media_dir)

        # Berechne relativen Pfad für DB
        if sound_csv_full_path and settings:
             try:
                  relative_csv_path_for_db = os.path.relpath(sound_csv_full_path, settings.MEDIA_ROOT).replace('\\', '/')
                  if "../" in relative_csv_path_for_db: raise ValueError("CSV Path outside media root")
             except Exception:
                  relative_csv_path_for_db = os.path.join(os.path.basename(stream_media_dir), os.path.basename(sound_csv_full_path)).replace('\\','/')

        # Aktualisiere Stream-Objekt mit Ergebnissen
        stream_obj.refresh_from_db()
        stream_obj.sound_csv_path = relative_csv_path_for_db # Relativen Pfad speichern
        stream_obj.avg_loudness = avg_loudness
        stream_obj.p90_loudness = p90_loudness
        stream_obj.max_loudness = max_loudness

        if sound_csv_full_path and os.path.exists(sound_csv_full_path):
            print(log_prefix + f"Starting initial highlight finding for {sound_csv_full_path}")
            find_highlights_by_loudness(
                sound_csv_path=sound_csv_full_path, # Voller Pfad zur Funktion
                video_path=video_path,
                stream_id=stream_id,
                user_name=user_name,
                threshold=LOUDNESS_THRESHOLD
            )
            stream_obj.analysis_status = 'COMPLETE'
        else:
            print(log_prefix + f"Sound analysis failed. Skipping highlight detection.")
            stream_obj.analysis_status = 'ERROR'

        stream_obj.save(update_fields=['analysis_status', 'sound_csv_path', 'avg_loudness', 'p90_loudness', 'max_loudness'])

    except Exception as e_main_thread:
         print(log_prefix + f"ERROR in main analysis thread function: {e_main_thread}")
         traceback.print_exc()
         try:
             if stream_obj: # Prüfe ob stream_obj existiert
                 stream_obj.refresh_from_db()
                 stream_obj.analysis_status = 'ERROR'
                 stream_obj.save(update_fields=['analysis_status'])
         except Exception as e_save: print(log_prefix + f"ERROR setting status to ERROR: {e_save}")

    finally:
        end_time_thread = time.time()
        duration_str = time.strftime('%H:%M:%S', time.gmtime(end_time_thread - start_time_thread))
        print(f"{log_prefix}--- Thread finished for Stream ID: {stream_id} (Duration: {duration_str}) ---\n")
        # CSV wird jetzt NICHT mehr automatisch gelöscht, da sie für Re-Generate gebraucht wird