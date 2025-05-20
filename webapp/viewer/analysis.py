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
import shutil  # <<< HINZUGEFÜGT: Import für shutil

try:
    from django.conf import settings
    from .models import Stream, StreamHighlight  # Stelle sicher, dass StreamHighlight importiert wird

    MODELS_AVAILABLE = True
except ImportError as e:
    print(f"FATAL ERROR in analysis.py: Cannot import Django components! ({e})")
    Stream = None
    StreamHighlight = None  # Sicherstellen, dass es definiert ist, auch wenn None
    MODELS_AVAILABLE = False
    settings = None  # settings auch als None definieren für den Fehlerfall

# --- Konfiguration ---
LOUDNESS_THRESHOLD = 0.2
CLIP_DURATION_S = 5  # Standard Clip-Dauer in Sekunden
TARGET_SAMPLE_RATE = 22050  # Standard Sample-Rate
SEGMENT_SECONDS = 0.5  # Dauer eines Analyse-Segments in Sekunden
HOP_SECONDS = 0.2  # Überlappung der Analyse-Segmente in Sekunden

# Temporäres Verzeichnis für Analyse-Artefakte (relativ zum MEDIA_ROOT)
# Stellt sicher, dass es nicht im Hauptverzeichnis des Projekts landet
TEMP_ANALYSIS_DIR_NAME = "analysis_temp_files"


class SoundDetectorSimplified:
    def __init__(self, stream_dataframe_name: str, base_media_dir: str):
        self.stream_dataframe_name = str(stream_dataframe_name)  # Sicherstellen, dass es ein String ist
        self.output_columns = ['start_time', 'end_time', 'sound_loudness']
        self.stream_features = pd.DataFrame([], columns=self.output_columns)
        self.sample_rate = TARGET_SAMPLE_RATE
        self.segment_seconds = SEGMENT_SECONDS
        self.hop_seconds = HOP_SECONDS
        # Eindeutiges temporäres Verzeichnis für diese Analyse-Instanz
        # Wird relativ zum MEDIA_ROOT erstellt, falls settings verfügbar sind
        if settings and settings.MEDIA_ROOT:
            self.temp_instance_dir = os.path.join(settings.MEDIA_ROOT, TEMP_ANALYSIS_DIR_NAME,
                                                  self.stream_dataframe_name)
        else:  # Fallback, falls settings nicht verfügbar (sollte im Django-Kontext nicht passieren)
            self.temp_instance_dir = os.path.join(TEMP_ANALYSIS_DIR_NAME, self.stream_dataframe_name)

        # Sicherstellen, dass das Basis-Ausgabeverzeichnis für CSVs existiert
        self.csv_output_dir = base_media_dir  # Das Stream-Verzeichnis (z.B. media/uploads/user/stream_id/)
        os.makedirs(self.csv_output_dir, exist_ok=True)
        os.makedirs(self.temp_instance_dir, exist_ok=True)

    def analyze_file(self, video_path):  # stream_media_dir entfernt, da jetzt im Konstruktor
        base_name = f"stream_{self.stream_dataframe_name}_loudness"
        # Temp-Audio-Datei im instanzspezifischen Temp-Ordner
        temp_audio_path = os.path.join(self.temp_instance_dir, f"{base_name}_temp_audio.wav")
        # CSV-Datei im Stream-Verzeichnis (csv_output_dir)
        output_csv_path = os.path.join(self.csv_output_dir, f"{base_name}_sound.csv")

        thread_id = threading.get_ident()
        log_prefix = f"[Thread-{thread_id} Analysis StreamID:{self.stream_dataframe_name}] "
        print(log_prefix + f"Starting sound analysis for: {video_path}")
        max_loudness_found = 0.0;
        avg_loudness = 0.0;
        p90_loudness = 0.0;
        p95_loudness = 0.0

        try:
            print(log_prefix + "Extracting audio...")
            t_start_extract = time.time()
            ffmpeg_path = getattr(settings, 'FFMPEG_PATH', 'ffmpeg')
            cmd_extract = [ffmpeg_path, '-i', video_path, '-vn', '-acodec', 'pcm_s16le',
                           '-ar', str(self.sample_rate), '-ac', '1', '-loglevel', 'error', temp_audio_path, '-y']
            subprocess.run(cmd_extract, check=True, capture_output=True, text=True, encoding='utf-8')
            print(log_prefix + f"Audio extracted (took {time.time() - t_start_extract:.2f}s) to {temp_audio_path}")

            print(log_prefix + "Loading audio...")
            t_start_load = time.time()
            sound, sr = librosa.load(temp_audio_path, sr=self.sample_rate, mono=True)
            print(
                log_prefix + f"Audio loaded (Duration: {librosa.get_duration(y=sound, sr=sr):.2f}s, took {time.time() - t_start_load:.2f}s)")

            segment_samples = int(self.segment_seconds * sr)
            hop_samples = int(self.hop_seconds * sr)
            results = []
            num_segments = max(0, (len(sound) - segment_samples) // hop_samples + 1)
            print(log_prefix + f"Analyzing loudness in approx. {num_segments} segments...")
            t_start_rms = time.time()
            all_rms_values = []

            for i in range(0, len(sound) - segment_samples + 1, hop_samples):
                segment = sound[i: i + segment_samples]
                current_time_sec = i / sr
                rms = librosa.feature.rms(y=segment)[0]
                mean_rms = np.mean(rms)
                all_rms_values.append(mean_rms)
                results.append(
                    {'start_time': current_time_sec, 'end_time': current_time_sec + self.segment_seconds,
                     'sound_loudness': mean_rms})

            print(log_prefix + f"Loudness analysis finished (took {time.time() - t_start_rms:.2f}s)")
            if not results:
                print(log_prefix + "No loudness segments generated.")
                return None, 0.0, 0.0, 0.0, 0.0

            self.stream_features = pd.DataFrame(results, columns=self.output_columns)
            self.stream_features.to_csv(output_csv_path, index=False)
            print(log_prefix + f"Loudness results saved to {output_csv_path}")

            loudness_series = pd.Series(all_rms_values)
            avg_loudness = loudness_series.mean() if not loudness_series.empty else 0.0
            p90_loudness = loudness_series.quantile(0.90) if not loudness_series.empty else 0.0
            p95_loudness = loudness_series.quantile(0.95) if not loudness_series.empty else 0.0
            max_loudness_found = loudness_series.max() if not loudness_series.empty else 0.0
            print(
                log_prefix + f"Loudness Stats: Avg={avg_loudness:.4f}, P90={p90_loudness:.4f}, P95={p95_loudness:.4f}, Max={max_loudness_found:.4f}")

            return output_csv_path, avg_loudness, p90_loudness, p95_loudness, max_loudness_found

        except FileNotFoundError:
            print(log_prefix + f"ERROR: ffmpeg command not found (path used: '{ffmpeg_path}').")
            return None, 0.0, 0.0, 0.0, 0.0
        except Exception as e:
            print(log_prefix + f"ERROR during simplified sound analysis: {e}")
            traceback.print_exc()
            return None, 0.0, 0.0, 0.0, 0.0
        finally:
            # Temporäre Audio-Datei löschen
            if os.path.exists(temp_audio_path):
                try:
                    os.remove(temp_audio_path)
                    print(log_prefix + f"Deleted temp audio file: {temp_audio_path}")
                except Exception as e_del_audio:
                    print(log_prefix + f"Warning: Could not delete temp audio file {temp_audio_path}: {e_del_audio}")
            # Temporäres Verzeichnis für diese Instanz löschen (wenn leer)
            try:
                if os.path.exists(self.temp_instance_dir) and not os.listdir(self.temp_instance_dir):
                    os.rmdir(self.temp_instance_dir)
                    print(log_prefix + f"Deleted empty temp instance directory: {self.temp_instance_dir}")
                elif os.path.exists(self.temp_instance_dir):  # Nur wenn es noch existiert und nicht leer ist
                    # Falls es komplexere Strukturen gäbe und man alles löschen wollte:
                    # shutil.rmtree(self.temp_instance_dir)
                    # print(log_prefix + f"Deleted temp analysis directory tree {self.temp_instance_dir}")
                    print(
                        log_prefix + f"Warning: Temp instance directory {self.temp_instance_dir} not empty, not deleted by rmdir.")
            except Exception as e_del_dir:
                print(
                    log_prefix + f"Warning: Could not delete temp analysis directory {self.temp_instance_dir}: {e_del_dir}")


def find_highlights_by_loudness(sound_csv_path, video_path, stream_id, user_name, stream_link_override, threshold,
                                clip_duration=CLIP_DURATION_S):
    thread_id = threading.get_ident()
    log_prefix = f"[Thread-{thread_id} HighlightFind StreamID:{stream_id}] "

    if not MODELS_AVAILABLE:
        print(log_prefix + "ERROR: Django Models not available in find_highlights. Cannot save highlights.")
        return []

    # Stream-Objekt holen, um stream_link zu verwenden, falls kein Override da ist (sollte aber da sein)
    # und um die Stream-ID für die Benennung zu haben (ist aber schon als stream_id Parameter da)
    stream_obj = None
    try:
        stream_obj = Stream.objects.get(id=stream_id)
        # Verwende den übergebenen stream_link_override. Dieser sollte vom Aufrufer (z.B. views.py)
        # korrekt auf den stream_link des Stream-Objekts gesetzt werden.
        link_for_highlights = stream_link_override
        if not link_for_highlights:  # Fallback, falls doch mal None übergeben wird
            link_for_highlights = stream_obj.stream_link
            print(
                log_prefix + f"Warning: stream_link_override was None, using stream_obj.stream_link: '{link_for_highlights}'")

    except Stream.DoesNotExist:
        print(
            log_prefix + f"ERROR: Stream object with ID {stream_id} not found. Cannot determine stream_link for highlights.")
        return []
    except Exception as e_db_stream:
        print(log_prefix + f"ERROR accessing DB for Stream ID {stream_id}: {e_db_stream}")
        return []

    print(
        log_prefix + f"Finding highlights (Threshold={threshold:.4f}, Clip={clip_duration}s) in {sound_csv_path} for StreamLink: '{link_for_highlights}'")
    try:
        df = pd.read_csv(sound_csv_path)
        if df.empty:
            print(log_prefix + "Sound CSV is empty. No highlights to find.")
            # Alte Highlights für diesen Link trotzdem löschen
            StreamHighlight.objects.filter(user_id=user_name, stream_link=link_for_highlights).delete()
            print(log_prefix + f"Deleted any old highlights for link '{link_for_highlights}' due to empty CSV.")
            return []

        loud_segments = df[df['sound_loudness'] > threshold]
        if loud_segments.empty:
            print(log_prefix + "No segments found above threshold.")
            StreamHighlight.objects.filter(user_id=user_name, stream_link=link_for_highlights).delete()
            print(
                log_prefix + f"Deleted any old highlights for link '{link_for_highlights}' as no new ones were found.")
            return []

        loud_start_times = loud_segments['start_time'].tolist()
        print(
            log_prefix + f"Found {len(loud_start_times)} segments above threshold. Extracting {clip_duration}s clips...")

        # Alte Highlights löschen, BEVOR neue erstellt werden
        old_highlights_query = StreamHighlight.objects.filter(user_id=user_name, stream_link=link_for_highlights)
        if old_highlights_query.exists():
            print(
                log_prefix + f"Deleting {old_highlights_query.count()} old highlight entries/files for link '{link_for_highlights}'...")
            for hl in old_highlights_query:
                try:
                    if hl.clip_link and settings and settings.MEDIA_ROOT:
                        # clip_link ist relativ zu MEDIA_ROOT
                        clip_full_path = os.path.join(settings.MEDIA_ROOT, hl.clip_link)
                        if os.path.exists(clip_full_path) and os.path.isfile(clip_full_path):
                            os.remove(clip_full_path)
                            print(log_prefix + f"  Deleted old clip file: {clip_full_path}")
                except Exception as e_del_old_file:
                    print(log_prefix + f"  Warning: Could not delete old clip file {hl.clip_link}: {e_del_old_file}")
            old_highlights_query.delete()
        else:
            print(log_prefix + f"No old highlights found for link '{link_for_highlights}' to delete.")

        extracted_clips_info = []
        # Clips sollen im selben Verzeichnis wie das Originalvideo landen
        # video_path ist der absolute Pfad zur Quelldatei, z.B. media/uploads/user/stream_id/stream_id.mp4
        output_dir_for_clips = os.path.dirname(video_path)
        # Relative Basis für DB-Speicherung (z.B. uploads/user/stream_id)
        relative_clip_dir_base = os.path.relpath(output_dir_for_clips, settings.MEDIA_ROOT).replace('\\', '/')

        last_clip_end_time = -float('inf')
        clip_counter = 0
        ffmpeg_path = getattr(settings, 'FFMPEG_PATH', 'ffmpeg')

        # Kurze Pause, um sicherzustellen, dass z.B. CSV-Datei-Handles freigegeben sind
        time.sleep(getattr(settings, 'CLIP_EXTRACTION_DELAY_S', 2))
        print(
            log_prefix + f"Waiting {getattr(settings, 'CLIP_EXTRACTION_DELAY_S', 2)} seconds before starting clip extraction to allow file handle release...")

        for idx, start_time_s in enumerate(loud_start_times):
            if start_time_s < last_clip_end_time:  # Überlappende Clips vermeiden
                continue

            # Startzeit des Clips leicht anpassen (z.B. 1 Sekunde vorher beginnen) für Kontext
            clip_actual_start_s = max(0, start_time_s - 1.0)

            # Zeitformat für ffmpeg: HH:MM:SS.mmm
            clip_start_time_ffmpeg = time.strftime('%H:%M:%S', time.gmtime(
                clip_actual_start_s)) + '.' + f"{int((clip_actual_start_s % 1) * 1000):03d}"
            # Dauer für ffmpeg (kann auch als float in Sekunden angegeben werden)
            clip_duration_ffmpeg = f"{clip_duration:.3f}"

            # Eindeutiger Clip-Name, basierend auf Stream-ID und Index/Zeitstempel
            clip_name = f"highlight_loud_{stream_obj.id}_{idx:03d}.mp4"  # z.B. highlight_loud_59_000.mp4
            clip_output_path_absolute = os.path.join(output_dir_for_clips, clip_name)
            clip_output_path_relative_to_media = os.path.join(relative_clip_dir_base, clip_name).replace('\\', '/')

            print(
                log_prefix + f"  Extracting clip {clip_counter + 1}: {clip_name} (Original Start: {start_time_s:.2f}s -> Effective FFmpeg Start: {clip_actual_start_s:.2f}s)")

            # FFmpeg Befehl zum Extrahieren (mit -c copy für Geschwindigkeit und -movflags +faststart)
            cmd = [ffmpeg_path,
                   '-ss', clip_start_time_ffmpeg,
                   '-i', video_path,
                   '-t', clip_duration_ffmpeg,  # Dauer des Clips
                   '-c', 'copy',  # Stream copy
                   '-map', '0',  # Alle Streams mappen
                   '-avoid_negative_ts', 'make_zero',  # Timing-Probleme vermeiden
                   '-movflags', '+faststart',  # Für Web-Streaming optimieren
                   '-loglevel', 'error',  # Weniger Output
                   clip_output_path_absolute,
                   '-y']  # Überschreibe existierende Datei

            print(log_prefix + f"  FFmpeg command: {' '.join(cmd)}")

            try:
                extract_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                                   encoding='utf-8')
                stdout, stderr = extract_process.communicate(timeout=60)  # Timeout für Clip-Extraktion

                if extract_process.returncode == 0:
                    if os.path.exists(clip_output_path_absolute) and os.path.getsize(
                            clip_output_path_absolute) > 1000:  # Mindestgröße > 1KB
                        print(
                            log_prefix + f"  Clip extracted successfully and seems valid: {clip_output_path_absolute}")
                        if MODELS_AVAILABLE:
                            StreamHighlight.objects.create(
                                user_id=user_name,
                                stream_link=link_for_highlights,  # Der korrekte Link für die Zuordnung
                                clip_link=clip_output_path_relative_to_media,  # Relativ zu MEDIA_ROOT
                                start_time=clip_actual_start_s  # Startzeit des Clips speichern
                            )
                            print(
                                log_prefix + f"  Highlight '{clip_output_path_relative_to_media}' saved to database for link '{link_for_highlights}'.")
                            extracted_clips_info.append(
                                {'path': clip_output_path_absolute, 'relative': clip_output_path_relative_to_media})
                            last_clip_end_time = clip_actual_start_s + clip_duration
                            clip_counter += 1
                    else:
                        print(
                            log_prefix + f"  ERROR: Clip extraction for {clip_name} seemed to succeed (ffmpeg RC 0) but file is missing or too small.")
                        if os.path.exists(clip_output_path_absolute): os.remove(clip_output_path_absolute)  # Aufräumen
                else:  # ffmpeg Fehler
                    print(
                        log_prefix + f"  ERROR extracting clip {clip_name} with ffmpeg. RC: {extract_process.returncode}")
                    if stderr: print(f"  FFmpeg stderr:\n{stderr.strip()}")
                    if stdout: print(f"  FFmpeg stdout:\n{stdout.strip()}")  # stdout kann auch nützlich sein
                    if os.path.exists(clip_output_path_absolute): os.remove(clip_output_path_absolute)  # Aufräumen

            except subprocess.TimeoutExpired:
                print(log_prefix + f"  ERROR: FFmpeg process timed out for clip {clip_name}.")
                if extract_process and extract_process.poll() is None: extract_process.kill(); extract_process.communicate()
                if os.path.exists(clip_output_path_absolute): os.remove(clip_output_path_absolute)
            except Exception as e_clip_extract:
                print(
                    log_prefix + f"  Unexpected ERROR during clip extraction or DB save for {clip_name}: {e_clip_extract}")
                traceback.print_exc()
                if os.path.exists(clip_output_path_absolute): os.remove(clip_output_path_absolute)

        return extracted_clips_info

    except FileNotFoundError:
        print(log_prefix + f"ERROR: Sound CSV file not found at {sound_csv_path}")
        return []
    except Exception as e:
        print(log_prefix + f"ERROR during highlight finding in thread: {e}")
        traceback.print_exc()
        return []


# --- Hauptfunktion für den Analyse-Thread (Upload oder nach Aufnahme/Download) ---
def run_analysis_and_extraction_thread(video_path, stream_id, user_name):
    thread_id = threading.get_ident()
    log_prefix = f"[Thread-{thread_id} MainAnalysis StreamID:{stream_id}] "  # Eindeutiger Prefix
    print(f"\n{log_prefix}--- Thread started for Stream ID: {stream_id}, Video: {video_path} ---")
    start_time_thread = time.time()

    if not MODELS_AVAILABLE:
        print(f"{log_prefix}--- Thread stopped early: Django Models not available. ---")
        return

    stream_obj = None
    relative_csv_path_for_db = None

    try:
        stream_obj = Stream.objects.get(id=stream_id)
        # Wichtig: stream_link muss hier bereits korrekt gesetzt sein (Kanalname oder Upload-Name)
        # damit Highlights korrekt zugeordnet werden können.
        if not stream_obj.stream_link:
            print(
                log_prefix + f"ERROR: stream_obj.stream_link is not set for Stream ID {stream_id}. Cannot proceed with analysis properly.")
            stream_obj.analysis_status = 'ERROR'
            stream_obj.save(update_fields=['analysis_status'])
            return

        stream_obj.analysis_status = 'PROCESSING'
        stream_obj.avg_loudness = None;
        stream_obj.p90_loudness = None
        stream_obj.p95_loudness = None;
        stream_obj.max_loudness = None
        stream_obj.sound_csv_path = None
        stream_obj.save(
            update_fields=['analysis_status', 'avg_loudness', 'p90_loudness', 'p95_loudness', 'max_loudness',
                           'sound_csv_path'])

        # Verzeichnis, in dem das Video liegt (z.B. media/uploads/user/stream_id/)
        # Dies wird als Basis für das Speichern der CSV-Datei verwendet.
        video_dir_absolute = os.path.dirname(video_path)

        sound_detector = SoundDetectorSimplified(stream_dataframe_name=str(stream_id),
                                                 base_media_dir=video_dir_absolute)
        sound_csv_full_path, avg_loudness, p90_loudness, p95_loudness, max_loudness = sound_detector.analyze_file(
            video_path)

        if sound_csv_full_path and settings and settings.MEDIA_ROOT:
            try:
                # Mache den Pfad relativ zu MEDIA_ROOT für die DB
                relative_csv_path_for_db = os.path.relpath(sound_csv_full_path, settings.MEDIA_ROOT).replace('\\', '/')
                if "../" in relative_csv_path_for_db:  # Sicherheitscheck
                    print(
                        log_prefix + f"ERROR: Calculated relative CSV path '{relative_csv_path_for_db}' is outside MEDIA_ROOT. Using full path as fallback for logging, but DB will be None.")
                    relative_csv_path_for_db = None  # Nicht in DB speichern, wenn ungültig
            except ValueError as e_relpath:  # Falls auf verschiedenen Laufwerken etc.
                print(
                    log_prefix + f"ERROR: Could not make CSV path relative: {e_relpath}. CSV path: {sound_csv_full_path}")
                relative_csv_path_for_db = None  # Nicht in DB speichern

        stream_obj.refresh_from_db()
        stream_obj.sound_csv_path = relative_csv_path_for_db
        stream_obj.avg_loudness = avg_loudness
        stream_obj.p90_loudness = p90_loudness
        stream_obj.p95_loudness = p95_loudness
        stream_obj.max_loudness = max_loudness

        if sound_csv_full_path and os.path.exists(sound_csv_full_path):
            print(
                log_prefix + f"Starting initial highlight finding. Stream.stream_link to be used: '{stream_obj.stream_link}'")
            find_highlights_by_loudness(
                sound_csv_path=sound_csv_full_path,
                video_path=video_path,
                stream_id=stream_id,
                user_name=user_name,
                stream_link_override=stream_obj.stream_link,  # Explizit übergeben
                threshold=LOUDNESS_THRESHOLD  # Globaler Default für erste Analyse
            )
            stream_obj.analysis_status = 'COMPLETE'
        else:
            print(log_prefix + f"Sound analysis failed or CSV not found. Skipping highlight detection.")
            stream_obj.analysis_status = 'ERROR'
            if not relative_csv_path_for_db and sound_csv_full_path:  # Wenn CSV erstellt, aber Pfad problematisch war
                print(
                    log_prefix + f"Note: Sound CSV was created at {sound_csv_full_path}, but DB path could not be set.")

        stream_obj.save(
            update_fields=['analysis_status', 'sound_csv_path', 'avg_loudness', 'p90_loudness', 'p95_loudness',
                           'max_loudness'])

    except Stream.DoesNotExist:
        print(log_prefix + f"ERROR: Stream object with ID {stream_id} was deleted during analysis.")
    except Exception as e_main_thread:
        print(log_prefix + f"ERROR in main analysis thread function: {e_main_thread}")
        traceback.print_exc()
        try:
            if stream_obj:  # Versuche, den Stream-Status auf Fehler zu setzen, falls er noch existiert
                stream_obj.refresh_from_db()  # Holen, falls von anderem Thread geändert
                stream_obj.analysis_status = 'ERROR'
                stream_obj.save(update_fields=['analysis_status'])
        except Exception as e_save_err:
            print(
                log_prefix + f"ERROR setting status to ERROR for stream {stream_id} after main thread error: {e_save_err}")
    finally:
        end_time_thread = time.time()
        duration_str = time.strftime('%H:%M:%S', time.gmtime(end_time_thread - start_time_thread))
        print(f"{log_prefix}--- Thread finished for Stream ID: {stream_id} (Duration: {duration_str}) ---\n")