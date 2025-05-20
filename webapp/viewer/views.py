# AI_Highlight_Clipper/webapp/viewer/views.py

from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse, HttpResponseRedirect
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login, authenticate
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.conf import settings
from django.core.exceptions import FieldError
from django.core.files.base import ContentFile

import os
import threading
import traceback
import numpy as np
import subprocess
import sys
import uuid
import shutil
import signal
import time
import pandas as pd
import time

from .models import Stream, StreamHighlight
from .forms import StreamUploadForm
from .analysis import SoundDetector
from . import twitch_api_client


# --- INDEX / LOGIN / HAUPTSEITE ---
def index(request):
    reg_form = UserCreationForm()
    twitch_vods = request.session.pop('twitch_vods_context', None)
    searched_channel_name = request.session.pop('searched_channel_name_context', None)
    search_attempted = request.session.pop('search_attempted_context', False)

    if request.method == 'POST':
        if 'login_submit' in request.POST:
            username_post = request.POST.get('username');
            password_post = request.POST.get('password')
            user_auth = authenticate(request, username=username_post, password=password_post)
            if user_auth is not None:
                login(request, user_auth);
                return redirect('index')
            else:
                return render(request, 'viewer/index.html', {'form': reg_form, 'login_error': True})
        elif 'register_submit' in request.POST:
            reg_form_posted = UserCreationForm(request.POST)
            if reg_form_posted.is_valid():
                user = reg_form_posted.save();
                login(request, user);
                print(f"Neuer User: {user.username}");
                return redirect('index')
            else:
                print(f"Reg.-Fehler: {reg_form_posted.errors.as_json()}");
                return render(request, 'viewer/index.html',
                              {'form': reg_form_posted})
    if request.user.is_authenticated:
        stream_data = Stream.objects.filter(user_id=request.user.username).order_by('-id')
        upload_form = StreamUploadForm()
        response_data = {
            "stream_data": stream_data,
            "name": request.user.username,
            "is_staff": request.user.is_staff,
            "upload_form": upload_form,
            "twitch_vods": twitch_vods,
            "searched_channel_name": searched_channel_name,
            "search_attempted": search_attempted,
        }
        return render(request, 'viewer/main.html', response_data)
    else:
        return render(request, 'viewer/index.html', {'form': reg_form})


# --- STREAM/VIDEO HOCHLADEN + Thread Start (mit FFmpeg Optimierung) ---
@login_required
def add_stream(request):
    if request.method == 'POST':
        form = StreamUploadForm(request.POST, request.FILES)
        if form.is_valid():
            if not request.FILES.get('video_file'):
                messages.error(request, "Bitte Videodatei auswählen.")
                return render(request, 'viewer/main.html', {'upload_form': form})

            new_stream = form.save(commit=False)
            new_stream.user_id = request.user.username

            if not new_stream.stream_name:
                new_stream.stream_name = os.path.splitext(new_stream.video_file.name)[0]

            new_stream.analysis_status = 'PENDING'
            new_stream.save()  # Save file to disk

            print(f"📁 Stream gespeichert: {new_stream.video_file.name}, ID: {new_stream.id}")

            # --- Zielverzeichnis erstellen ---
            stream_id = new_stream.id
            user_id = request.user.username
            relative_dir = os.path.join('uploads', user_id, str(stream_id))
            absolute_dir = os.path.join(settings.MEDIA_ROOT, relative_dir)
            os.makedirs(absolute_dir, exist_ok=True)

            # --- Datei verschieben/kopieren ---
            video_dest = os.path.join(absolute_dir, f"{stream_id}.mp4")
            try:
                shutil.move(new_stream.video_file.path, video_dest)
                new_stream.video_file.name = os.path.join(relative_dir, f"{stream_id}.mp4").replace('\\', '/')
                new_stream.save(update_fields=['video_file'])
                print(f"📦 Video verschoben nach: {video_dest}")
            except Exception as e:
                print(f"❌ Fehler beim Verschieben: {e}")
                new_stream.analysis_status = 'ERROR'
                new_stream.save(update_fields=['analysis_status'])
                messages.error(request, "Fehler beim Verschieben der Videodatei.")
                return redirect('index')

            messages.success(request, "Video hochgeladen. Highlights können jetzt generiert werden.")
            return redirect('index')
        else:
            messages.error(request, "Fehler beim Hochladen des Videos.")
            return render(request, 'viewer/main.html', {'upload_form': form})
    else:
        return redirect('index')


@login_required
@require_POST
def generate_highlights_view(request, stream_id):
    stream = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    user_name = request.user.username

    try:
        video_path = stream.video_file.path
        stream_dir = os.path.dirname(video_path)
        sound_csv_path = os.path.join(stream_dir, f"{stream_id}_sound.csv")

        labels_list = ['Laughter', 'Cheering', 'Gunshot', 'Music', 'Speech', 'Dog', 'Crowd',
                       'Explosion', 'Applause', 'Scream', 'Laugh', 'Shout', 'Car', 'Siren']
        columns = ['start_time', 'end_time', 'sound_loudness'] + labels_list
        empty_df = pd.DataFrame({col: [] for col in columns})
        empty_df = empty_df.astype({col: 'float' for col in columns[2:]})

        if not os.path.exists(sound_csv_path):
            print(f"🎧 Starte SoundDetector für: {video_path}")
            detector = SoundDetector(
                channel_name=user_name,
                stream_features=empty_df,
                stream_dataframe_name=str(stream_id),
                video_path=video_path
            )
            detector.start()

            for _ in range(60):
                if os.path.exists(sound_csv_path):
                    break
                time.sleep(1)

        if not os.path.exists(sound_csv_path):
            messages.error(request, "Analyse fehlgeschlagen – keine CSV gefunden.")
            return redirect('stream', stream_id=stream_id)

        df = pd.read_csv(sound_csv_path)
        df['highlight_score'] = (
            df['sound_loudness'] * 1.5 +
            df['Laughter'] * 3 +
            df['Gunshot'] * 4 +
            df['Explosion'] * 2 +
            df['Scream'] * 2 +
            df['Applause'] * 1.5 +
            df['Cheering'] * 1.5
        )
        top_clips = df.sort_values(by='highlight_score', ascending=False).head(3)

        # === Gründe berechnen ===
        label_de = {
            'Laughter': 'Lachen', 'Cheering': 'Jubel', 'Gunshot': 'Schuss', 'Music': 'Musik',
            'Speech': 'Sprache', 'Dog': 'Hund', 'Crowd': 'Menschenmenge', 'Explosion': 'Explosion',
            'Applause': 'Applaus', 'Scream': 'Schrei', 'Laugh': 'Lachen', 'Shout': 'Rufen',
            'Car': 'Auto', 'Siren': 'Sirene'
        }

        def extract_reason(row, threshold=0.2):
            return ", ".join([label_de[col] for col in labels_list if getattr(row, col) > threshold]) or "Allgemeine Lautstärke"

        top_clips['reason'] = top_clips.apply(extract_reason, axis=1)

        # === Highlights-Ordner vorbereiten ===
        media_relative_dir = os.path.join('uploads', user_name, str(stream_id), 'highlights')
        media_full_dir = os.path.join(settings.MEDIA_ROOT, media_relative_dir)
        os.makedirs(media_full_dir, exist_ok=True)

        StreamHighlight.objects.filter(user_id=user_name, stream_link=stream.stream_link).delete()

        def sec_to_ts(sec):
            return time.strftime('%H:%M:%S', time.gmtime(sec)) + '.0'

        reasons_summary = []

        for idx, row in enumerate(top_clips.itertuples()):
            start_ts = sec_to_ts(row.start_time)
            duration = str(row.end_time - row.start_time)

            clip_filename = f"highlight_{idx + 1}.mp4"
            clip_path = os.path.join(media_full_dir, clip_filename)

            subprocess.run([
                "ffmpeg", "-ss", start_ts, "-i", video_path,
                "-t", duration, "-c:v", "copy", "-c:a", "copy",
                clip_path, "-y"
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            StreamHighlight.objects.create(
                user_id=user_name,
                stream_link=stream.stream_link,
                clip_link=os.path.join(media_relative_dir, clip_filename).replace('\\', '/'),
                reason=row.reason
            )

            # Grund merken
            reasons_summary.append(f"{clip_filename}: {row.reason}")
            print(f"📌 Highlight {clip_filename} → Grund: {row.reason}")

        messages.success(request, f"{len(top_clips)} Highlights wurden erfolgreich generiert.")
        for reason in reasons_summary:
            messages.info(request, reason)

    except Exception as e:
        print("❌ Fehler bei Highlight-Generierung:", e)
        traceback.print_exc()
        messages.error(request, "Fehler bei der Highlight-Generierung.")

    return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('stream', args=[stream_id])))


# --- TWITCH STREAM AUFNAHME STARTEN ---
@login_required
def record_stream_view(request):
    if request.method == 'POST':
        twitch_username = request.POST.get('twitch_username', '').strip()
        quality = request.POST.get('quality', '480p')
        if not twitch_username:
            messages.error(request, "Bitte Kanalnamen eingeben.")
            return redirect('index')

        client_id = getattr(settings, 'TWITCH_CLIENT_ID', None)
        client_secret = getattr(settings, 'TWITCH_CLIENT_SECRET', None)
        recorder_script_path = getattr(settings, 'TWITCH_RECORDER_SCRIPT_PATH', None)
        ffmpeg_path = getattr(settings, 'FFMPEG_PATH', 'ffmpeg')
        streamlink_path = getattr(settings, 'STREAMLINK_PATH', 'streamlink')

        if not all([client_id, client_secret, recorder_script_path]) or not os.path.exists(recorder_script_path):
            messages.error(request, "Fehler: Twitch-Konfiguration oder Recorder-Skript fehlt.")
            return redirect('index')

        flags_dir_path = os.path.join(settings.BASE_DIR, 'scripts', 'recorder_flags')
        try:
            os.makedirs(flags_dir_path, exist_ok=True)
        except OSError as e_mkdir:
            print(f"FEHLER: Konnte Flag-Verzeichnis nicht erstellen: {flags_dir_path}, Error: {e_mkdir}")
            messages.error(request, "Systemfehler: Konnte benötigtes Verzeichnis nicht erstellen.")
            return redirect('index')

        try:
            stream_obj = Stream.objects.create(
                user_id=request.user.username,
                stream_link=twitch_username.lower(),
                stream_name=f"Aufnahme: {twitch_username}",
                analysis_status='RECORDING_SCHEDULED'
            )
            stream_id = stream_obj.id
            print(f"Created Stream object ID {stream_id} for recording '{twitch_username}'")
        except Exception as e_db_create:
            print(f"ERROR creating Stream object: {e_db_create}");
            traceback.print_exc()
            messages.error(request, "Datenbankfehler beim Erstellen des Streams.")
            return redirect('index')

        video_full_path = None
        try:
            user_id_part = str(request.user.username)
            stream_id_part = str(stream_id)
            output_filename = f"{stream_id}.mp4"
            relative_dir = os.path.join('uploads', user_id_part, stream_id_part)
            absolute_dir = os.path.join(settings.MEDIA_ROOT, relative_dir)
            os.makedirs(absolute_dir, exist_ok=True)
            video_full_path = os.path.join(absolute_dir, output_filename)
            relative_file_path = os.path.join(relative_dir, output_filename).replace('\\', '/')
            stream_obj.video_file.name = relative_file_path
            print(f"Target recording path (absolute for script): {video_full_path}")
            print(f"Setting video_file.name in DB to (relative to MEDIA_ROOT): {stream_obj.video_file.name}")
            stream_obj.save(update_fields=['video_file'])
        except Exception as e_path:
            print(f"ERROR creating recording path or saving to DB: {e_path}");
            traceback.print_exc()
            messages.error(request, "Pfadfehler oder DB-Fehler beim Speichern des Dateipfads.")
            stream_obj.delete()
            return redirect('index')

        command = [
            sys.executable, recorder_script_path,
            '--username', twitch_username,
            '--quality', quality,
            '--uid', str(stream_id),
            '--output-path', video_full_path,
            '--client-id', client_id,
            '--client-secret', client_secret,
            '--ffmpeg-path', ffmpeg_path,
            '--streamlink-path', streamlink_path
        ]
        print(f"Starting background recorder process for Stream ID {stream_id}:")
        print(f"Command: {' '.join(command)}")

        try:
            process_creation_flags = 0
            if os.name == 'nt':
                process_creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
            process = subprocess.Popen(command, creationflags=process_creation_flags)
            stream_obj.recorder_pid = process.pid
            stream_obj.analysis_status = 'RECORDING'
            stream_obj.save(update_fields=['recorder_pid', 'analysis_status'])
            print(
                f"Recorder process started PID: {process.pid} with creationflags={process_creation_flags}. DB updated.")
            messages.success(request,
                             f"Aufnahme für '{twitch_username}' (ID: {stream_id}) gestartet (PID: {process.pid}).")
        except Exception as e_popen:
            print(f"ERROR starting recorder process: {e_popen}");
            traceback.print_exc()
            messages.error(request, "Fehler beim Starten des Aufnahme-Prozesses.")
            stream_obj.analysis_status = 'ERROR'
            stream_obj.recorder_pid = None
            stream_obj.save(update_fields=['analysis_status', 'recorder_pid'])
        return redirect('index')
    else:
        messages.warning(request, "Ungültige Anfrage.")
        return redirect('index')


# --- TWITCH AUFNAHME STOPPEN (Angepasste Wartezeit) ---
@login_required
@require_POST
def stop_recording_view(request, stream_id):
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    pid_to_kill = stream_obj.recorder_pid
    log_prefix = f"[Stop Request View Stream {stream_id}, PID: {pid_to_kill}] "
    print(log_prefix + "Attempting to stop recording.")

    if not pid_to_kill:
        print(log_prefix + "No PID found in database. Cannot stop.")
        messages.warning(request, f"Keine laufende Aufnahme-PID für Stream {stream_id} gefunden.")
        if stream_obj.analysis_status == 'RECORDING':
            stream_obj.analysis_status = 'ERROR'
            stream_obj.save(update_fields=['analysis_status'])
        return redirect('index')

    stop_successful = False
    process_was_found = True
    # Reduzierte Wartezeit nach dem Senden des Signals
    graceful_shutdown_wait_s = getattr(settings, 'RECORDER_GRACEFUL_SHUTDOWN_WAIT_S', 10)

    if not stop_successful:
        print(
            log_prefix + f"Attempting graceful shutdown (Signal: SIGINT/CTRL_C_EVENT, Wait: {graceful_shutdown_wait_s}s)...")
        try:
            if os.name == 'nt':
                os.kill(pid_to_kill, signal.CTRL_C_EVENT)
            else:
                os.kill(pid_to_kill, signal.SIGINT)
            print(log_prefix + "Signal sent. Waiting for process to terminate...")
            time.sleep(graceful_shutdown_wait_s)  # Angepasste Wartezeit
            try:
                os.kill(pid_to_kill, 0)
                print(log_prefix + f"Process {pid_to_kill} still exists after Signal. Proceeding.")
            except OSError:
                print(log_prefix + f"Process {pid_to_kill} successfully terminated after Signal.")
                stop_successful = True;
                process_was_found = False
        except ProcessLookupError:
            print(log_prefix + f"Process {pid_to_kill} not found (ProcessLookupError). Already stopped?");
            stop_successful = True;
            process_was_found = False;
            messages.warning(request, f"Aufnahme für Stream {stream_id} war bereits beendet.")
        except OSError as e:
            print(log_prefix + f"OS-Error sending Signal or checking process: {e}. Trying next method.");
        except Exception as e_sig:
            print(log_prefix + f"Unexpected error sending Signal: {e_sig}\n{traceback.format_exc()}");

    # ... (Rest der Stopp-Logik für taskkill etc. bleibt im Wesentlichen gleich) ...
    if not stop_successful and process_was_found and os.name == 'nt':
        print(log_prefix + "Attempting 'taskkill /PID ... /T' (without /F) for Windows...")
        try:
            result = subprocess.run(['taskkill', '/PID', str(pid_to_kill), '/T'],
                                    capture_output=True, text=False, check=False, timeout=15)
            stderr_str = result.stderr.decode(sys.getfilesystemencoding(), errors='replace') if result.stderr else ""

            if result.returncode == 0:
                print(log_prefix + f"Taskkill /T sent. Waiting (e.g., 10s)...");
                time.sleep(10)  # Kurze Wartezeit
                try:
                    os.kill(pid_to_kill, 0)
                except OSError:
                    stop_successful = True; process_was_found = False; print(
                        log_prefix + "Process terminated after soft taskkill /T.")
            elif result.returncode == 128 or "nicht gefunden" in stderr_str.lower() or "not found" in stderr_str.lower():
                print(log_prefix + f"Process PID {pid_to_kill} not found by taskkill /T. stderr: {stderr_str}")
                stop_successful = True;
                process_was_found = False
                messages.warning(request, f"Aufnahme für Stream {stream_id} war bereits beendet.")
            else:
                stdout_str = result.stdout.decode(sys.getfilesystemencoding(),
                                                  errors='replace') if result.stdout else ""
                print(
                    log_prefix + f"Soft taskkill /T FAILED. RC: {result.returncode}, stderr: {stderr_str}, stdout: {stdout_str}. Trying forceful.")
        except subprocess.TimeoutExpired:
            print(log_prefix + "Soft taskkill /T timed out. Trying forceful kill.")
        except FileNotFoundError:
            print(log_prefix + "ERROR: taskkill command not found.");
            messages.error(request, "Systemfehler: 'taskkill' nicht gefunden.")
            stream_obj.analysis_status = 'ERROR';
        except Exception as e_taskkill_soft_t:
            print(
                log_prefix + f"Unexpected error during soft taskkill /T: {e_taskkill_soft_t}\n{traceback.format_exc()}")

    if not stop_successful and process_was_found:
        kill_cmd_str = ""
        try:
            if os.name == 'nt':
                kill_cmd_str = f"taskkill /F /PID {pid_to_kill} /T";
                print(log_prefix + f"Attempting forceful '{kill_cmd_str}'...")
                kill_command_force = ['taskkill', '/F', '/PID', str(pid_to_kill), '/T'];
                result = subprocess.run(kill_command_force, capture_output=True, text=False, check=False, timeout=10)
                stderr_str_force = result.stderr.decode(sys.getfilesystemencoding(),
                                                        errors='replace') if result.stderr else ""
                if result.returncode == 0:
                    print(log_prefix + f"Successfully sent FORCE kill signal.");
                    messages.warning(request, f"Aufnahme GEWALTSAM gestoppt.");
                    stop_successful = True;
                    process_was_found = False
                elif result.returncode == 128 or "nicht gefunden" in stderr_str_force.lower() or "not found" in stderr_str_force.lower():
                    print(log_prefix + f"Process not found by forceful taskkill. stderr: {stderr_str_force}");
                    stop_successful = True;
                    process_was_found = False;
                    messages.warning(request, f"Aufnahme bereits beendet (force kill).")
                else:
                    print(log_prefix + f"Error FORCE stopping. RC: {result.returncode}, stderr: {stderr_str_force}");
                    messages.error(request, f"Fehler beim gewaltsamen Stoppen.");
                    stream_obj.analysis_status = 'ERROR_STOP_FAILED';
            else:
                kill_cmd_str = f"kill -9 {pid_to_kill}";
                print(log_prefix + "Attempting forceful SIGKILL (Unix)...");
                os.kill(pid_to_kill, signal.SIGKILL);
                messages.warning(request, f"Aufnahme mit SIGKILL beendet.");
                stop_successful = True;
                process_was_found = False
        except ProcessLookupError:
            print(log_prefix + f"Process not found for {kill_cmd_str}.");
            stop_successful = True;
            process_was_found = False;
            if not messages.get_messages(request): messages.warning(request, f"Aufnahme bereits beendet (force kill).")
        except subprocess.TimeoutExpired:
            print(log_prefix + f"Forceful {kill_cmd_str} timed out.");
            stop_successful = True;
            process_was_found = False
        except FileNotFoundError:
            print(log_prefix + f"ERROR: {kill_cmd_str.split()[0]} not found.");
            messages.error(request, f"Systemfehler: '{kill_cmd_str.split()[0]}' nicht gefunden.");
            if stream_obj.analysis_status != 'ERROR_STOP_FAILED': stream_obj.analysis_status = 'ERROR_STOP_FAILED';
        except Exception as e_kill_force:
            print(log_prefix + f"Unexpected error force stopping: {e_kill_force}\n{traceback.format_exc()}");
            messages.error(request, f"Unerwarteter Systemfehler beim Stoppen.");
            if stream_obj.analysis_status != 'ERROR_STOP_FAILED': stream_obj.analysis_status = 'ERROR_STOP_FAILED';

    stream_obj.recorder_pid = None
    final_status_for_stream = stream_obj.analysis_status
    analysis_started = False

    if stop_successful or not process_was_found:
        video_full_path = None;
        video_file_valid = False
        if stream_obj.video_file and stream_obj.video_file.name:
            try:
                video_full_path = os.path.join(settings.MEDIA_ROOT, stream_obj.video_file.name)
                print(log_prefix + f"Checking for video file: {video_full_path}")
                if os.path.exists(video_full_path) and os.path.getsize(video_full_path) > 1000:  # Mindestgröße
                    print(log_prefix + "Video file exists and has content.")
                    video_file_valid = True
                else:
                    print(log_prefix + "Video file NOT found or empty.");
                    final_status_for_stream = 'ERROR_NO_FILE';
                    messages.error(request, f"Aufnahme gestoppt, aber keine gültige Videodatei.")
            except Exception as e_path_check:
                print(log_prefix + f"Error checking video file path: {e_path_check}");
                final_status_for_stream = 'ERROR';
                messages.error(request, f"Fehler beim Prüfen der Videodatei.")

            if video_file_valid:
                final_status_for_stream = 'DOWNLOAD_COMPLETE'
                print(
                    log_prefix + f"Status wird auf '{final_status_for_stream}' gesetzt – Analyse wird NICHT automatisch gestartet.")
                messages.success(request, f"Aufnahme beendet. Video gespeichert.")

        else:
            print(log_prefix + "No video_file.name in DB. Cannot start analysis.");
            final_status_for_stream = 'ERROR_NO_FILE';
            messages.error(request, f"Aufnahme gestoppt, Dateipfad unbekannt.")
    elif final_status_for_stream not in ['ERROR', 'ERROR_STOP_FAILED', 'ERROR_NO_FILE']:
        final_status_for_stream = 'ERROR_STOP_FAILED';
        print(log_prefix + "Stop command failed, status to ERROR_STOP_FAILED.")

    stream_obj.analysis_status = final_status_for_stream
    stream_obj.save(update_fields=['analysis_status', 'recorder_pid'])
    print(log_prefix + f"Final DB status: {stream_obj.analysis_status}. PID removed.")
    return redirect('index')


# --- CLIP HINZUFÜGEN ---
def add_clip(request):
    print("WARNUNG: /add_clip/ aufgerufen.");
    return JsonResponse({'status': 'add_clip endpoint reached (likely unused)'})


# --- STREAM DETAILSEITE (Highlights) ---
@login_required
def stream(request, stream_id):
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    clips_data = StreamHighlight.objects.filter(
        user_id=request.user.username,
        stream_link=stream_obj.stream_link
    ).order_by('start_time')

    for clip in clips_data:
        if clip.clip_link and not clip.clip_link.startswith(('http://', 'https://', '/')):
            try:
                clip.media_url = os.path.join(settings.MEDIA_URL, clip.clip_link).replace('\\', '/')
            except Exception:
                clip.media_url = None
        else:
            clip.media_url = clip.clip_link
    response_data = {
        "name": request.user.username,
        "stream": stream_obj,
        "is_staff": request.user.is_staff,
        "clips_data": clips_data
    }
    return render(request, 'viewer/stream.html', response_data)


# --- STREAM LÖSCHEN ---
@login_required
@require_POST
def delete_stream(request, stream_id):
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    stream_title_for_log = stream_obj.stream_name or stream_obj.stream_link or f"ID {stream_id}"
    print(f"Attempting delete Stream ID: {stream_id} ('{stream_title_for_log}') for User: {request.user.username}")

    stream_dir_to_delete_absolute = None
    video_file_name_to_delete = None
    sound_csv_relative_path = stream_obj.sound_csv_path
    sound_csv_absolute_path = None

    if stream_obj.video_file and stream_obj.video_file.name:
        video_file_name_to_delete = stream_obj.video_file.name
        relative_video_dir = os.path.dirname(video_file_name_to_delete)
        expected_dir_pattern = os.path.join('uploads', str(stream_obj.user_id), str(stream_obj.id)).replace('\\', '/')
        if relative_video_dir.startswith(expected_dir_pattern):
            stream_dir_to_delete_absolute = os.path.join(settings.MEDIA_ROOT, relative_video_dir)
            print(f"Determined stream directory for deletion: {stream_dir_to_delete_absolute}")
        else:
            print(
                f"Warning: Video file directory '{relative_video_dir}' does not match expected structure '{expected_dir_pattern}'.")
            stream_dir_to_delete_absolute = None
    else:
        print("No video_file.name set for this stream.")

    if sound_csv_relative_path:
        sound_csv_absolute_path = os.path.join(settings.MEDIA_ROOT, sound_csv_relative_path)

    highlights = StreamHighlight.objects.filter(user_id=stream_obj.user_id, stream_link=stream_obj.stream_link)
    print(f"Deleting {highlights.count()} highlight DB entries for Stream link: '{stream_obj.stream_link}'...")
    highlights.delete()
    stream_obj.delete()
    print(f"Stream DB entry (ID: {stream_id}) deleted.")

    deleted_physical_content = False
    if os.name == 'nt': time.sleep(0.5)

    if stream_dir_to_delete_absolute and os.path.exists(stream_dir_to_delete_absolute) and os.path.isdir(
            stream_dir_to_delete_absolute):
        if os.path.normpath(stream_dir_to_delete_absolute) != os.path.normpath(settings.MEDIA_ROOT) and \
                settings.MEDIA_ROOT in os.path.normpath(stream_dir_to_delete_absolute):
            try:
                shutil.rmtree(stream_dir_to_delete_absolute)
                print(f"Successfully deleted stream directory: {stream_dir_to_delete_absolute}")
                messages.success(request, f"Stream '{stream_title_for_log}' und Verzeichnis gelöscht.")
                deleted_physical_content = True
            except OSError as e_rmdir:
                print(f"Error deleting directory {stream_dir_to_delete_absolute}: {e_rmdir}")
                messages.error(request, f"Stream gelöscht, aber Verzeichnis konnte nicht entfernt werden: {e_rmdir}")
        else:
            print(f"SAFETY PREVENTED deletion of directory '{stream_dir_to_delete_absolute}'.")
            stream_dir_to_delete_absolute = None

    if not deleted_physical_content:
        files_deleted_in_fallback = False
        if video_file_name_to_delete:
            video_file_absolute_path = os.path.join(settings.MEDIA_ROOT, video_file_name_to_delete)
            if os.path.exists(video_file_absolute_path) and os.path.isfile(video_file_absolute_path):
                try:
                    os.remove(video_file_absolute_path);
                    print(f"Deleted video (fallback): {video_file_absolute_path}");
                    files_deleted_in_fallback = True
                except OSError as e_rmfile:
                    print(f"Error deleting video (fallback): {e_rmfile}")
        if sound_csv_absolute_path and os.path.exists(sound_csv_absolute_path) and os.path.isfile(
                sound_csv_absolute_path):
            try:
                os.remove(sound_csv_absolute_path);
                print(f"Deleted CSV (fallback): {sound_csv_absolute_path}");
                files_deleted_in_fallback = True
            except OSError as e_rmcsv:
                print(f"Error deleting CSV (fallback): {e_rmcsv}")
        if files_deleted_in_fallback and not messages.get_messages(request):
            messages.success(request, f"Stream '{stream_title_for_log}' und zugehörige Dateien gelöscht.")
        elif not files_deleted_in_fallback and not messages.get_messages(request):
            if (video_file_name_to_delete or sound_csv_absolute_path):
                messages.warning(request,
                                 f"Stream '{stream_title_for_log}' gelöscht, aber Dateien nicht gefunden/löschbar.")
            else:
                messages.success(request, f"Stream '{stream_title_for_log}' gelöscht (keine Dateien bekannt).")
    print(f"Deletion process completed for former Stream ID: {stream_id}")
    return redirect('index')


# --- VIDEOPLAYER SEITE ---
@login_required
def video_player_view(request, stream_id):
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    video_url = None;
    video_exists = False;
    error_message = None
    log_prefix = f"[VideoPlayer View Stream {stream_id}] "
    print(
        log_prefix + f"Status: {stream_obj.analysis_status}, Video File: {stream_obj.video_file.name if stream_obj.video_file else 'N/A'}")

    if stream_obj.video_file and stream_obj.video_file.name:
        try:
            video_url = stream_obj.video_file.url;
            video_exists = True
            print(log_prefix + f"Successfully obtained video_url: {video_url}")
        except Exception as e_url:
            print(log_prefix + f"ERROR accessing video_file.url: {e_url}");
            traceback.print_exc()
            error_message = "Fehler beim Abrufen der Video-URL.";
            video_exists = False;
            video_url = None
    else:
        print(log_prefix + "video_file.name is not set or video_file is None.")
        status_map = {
            'RECORDING': "Video wird noch aufgenommen.",
            'PENDING': "Video wird gerade verarbeitet.", 'PROCESSING': "Video wird gerade verarbeitet.",
            'RECORDING_SCHEDULED': "Aufnahme ist geplant.", 'DOWNLOAD_SCHEDULED': "Download ist geplant.",
            'DOWNLOADING': "Video lädt herunter.",
            'MANUALLY_STOPPED': "Aufnahme manuell gestoppt. Verarbeitung fehlt oder fehlgeschlagen.",
            'ERROR': "Fehler bei Aufnahme/Verarbeitung.", 'ERROR_NO_FILE': "Fehler: Videodatei nicht gefunden.",
            'ERROR_STOP_FAILED': "Fehler: Aufnahme konnte nicht gestoppt werden.",
            'ERROR_DOWNLOAD': "Fehler beim Download.", 'ERROR_DOWNLOAD_TIMEOUT': "Download Timeout."
        }
        error_message = status_map.get(stream_obj.analysis_status,
                                       "Kein Videopfad gespeichert oder unbekannter Status.")

    if not video_exists and stream_obj.video_file and stream_obj.video_file.name:  # Fallback, falls .url nicht ging aber Datei da ist
        try:
            abs_path_check = os.path.join(settings.MEDIA_ROOT, stream_obj.video_file.name)
            if os.path.exists(abs_path_check):
                print(log_prefix + f"File exists at {abs_path_check}. Constructing URL manually.")
                video_url = os.path.join(settings.MEDIA_URL, stream_obj.video_file.name).replace('\\', '/');
                video_exists = True
                if not error_message: error_message = "Video-URL manuell konstruiert."
            else:
                print(log_prefix + f"File NOT at {abs_path_check}.");
                if not error_message: error_message = "Videodatei nicht im System gefunden."
                video_exists = False;
                video_url = None
        except Exception as e_abs_check:
            print(log_prefix + f"Error during absolute path check: {e_abs_check}");
            if not error_message: error_message = "Fehler bei Überprüfung des Videopfads."
            video_exists = False;
            video_url = None

    if stream_obj.analysis_status == 'COMPLETE' and not video_exists and not error_message:
        error_message = "Analyse abgeschlossen, aber Video nicht ladbar. Datei evtl. entfernt."
        print(log_prefix + "Inconsistent: COMPLETE, but no video_exists/error_message.")

    context = {'stream': stream_obj, 'name': request.user.username, 'is_staff': request.user.is_staff,
               'video_url': video_url, 'video_exists': video_exists, 'error_message': error_message}
    return render(request, 'viewer/video_player.html', context)


# --- GENERATOR SEITE ---
@login_required
def generator_view(request, stream_id):
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    clips_data = StreamHighlight.objects.none();
    error_message_highlights = None
    log_prefix = f"[Generator View Stream {stream_id}] "
    print(log_prefix + f"Stream Link for highlights: '{stream_obj.stream_link}'")
    try:
        query = StreamHighlight.objects.filter(user_id=request.user.username, stream_link=stream_obj.stream_link)
        clips_data = query.order_by('start_time')
        print(log_prefix + f"Found {clips_data.count()} highlights for link '{stream_obj.stream_link}'.")
        for clip in clips_data:
            if clip.clip_link and not clip.clip_link.startswith(('http://', 'https://', '/')):
                try:
                    clip.media_url = os.path.join(settings.MEDIA_URL, clip.clip_link).replace('\\', '/')
                except Exception:
                    clip.media_url = None
            else:
                clip.media_url = clip.clip_link
    except FieldError as e_filter:
        print(log_prefix + f"FieldError: {e_filter}");
        traceback.print_exc()
        error_message_highlights = f"Fehler beim Sortieren der Highlights: {e_filter}."
        try:
            clips_data = StreamHighlight.objects.filter(user_id=request.user.username,
                                                        stream_link=stream_obj.stream_link)
        except:
            clips_data = StreamHighlight.objects.none()
    except Exception as e_clips:
        print(log_prefix + f"Unexpected error querying highlights: {e_clips}");
        traceback.print_exc()
        error_message_highlights = "Unerwarteter Fehler beim Laden der Highlights."

    current_form_threshold = None
    if stream_obj.p95_loudness is not None and stream_obj.p95_loudness > 0:
        current_form_threshold = stream_obj.p95_loudness
    elif stream_obj.p90_loudness is not None and stream_obj.p90_loudness > 0:
        current_form_threshold = stream_obj.p90_loudness

    if current_form_threshold is not None:
        current_form_threshold = max(0.001, current_form_threshold)
    else:
        current_form_threshold = 0.01  # Fallback, z. B. 0.01 oder leer lassen

    context = {'stream': stream_obj, 'name': request.user.username, 'is_staff': request.user.is_staff,
               'clips_data': clips_data, 'current_threshold': f"{current_form_threshold:.4f}",
               'error_message_highlights': error_message_highlights}
    return render(request, 'viewer/generator.html', context)


# --- HIGHLIGHTS NEU GENERIEREN ---
@login_required
@require_POST
def regenerate_highlights_view(request, stream_id):
    messages.warning(request, "Neugenerierung von Highlights ist deaktiviert.")
    return redirect('generator', stream_id=stream_id)



# --- NEUE VIEWS FÜR TWITCH VOD IMPORT ---
@login_required
def fetch_twitch_vods_view(request):
    twitch_vods = []
    searched_channel_name = request.POST.get('twitch_channel_name', '').strip()

    if request.method == 'POST':
        if not searched_channel_name:
            messages.error(request, "Bitte einen Twitch Kanalnamen eingeben.")
            request.session['twitch_vods_context'] = []
            request.session['searched_channel_name_context'] = ''
            request.session['search_attempted_context'] = True
            return redirect('index')

        print(f"[Fetch VODs View] User '{request.user.username}' searching VODs for: {searched_channel_name}")
        user_id = twitch_api_client.get_user_id_by_login(searched_channel_name)

        vod_results_for_session = []
        search_attempted_for_session = True

        if user_id:
            print(f"[Fetch VODs View] Found User ID: {user_id} for {searched_channel_name}")
            fetched_vods = twitch_api_client.get_user_vods(user_id, max_results=12)
            if fetched_vods:
                print(f"[Fetch VODs View] Fetched {len(fetched_vods)} VODs.")
                vod_results_for_session = fetched_vods
                messages.success(request, f"{len(fetched_vods)} VODs für '{searched_channel_name}' gefunden.")
            else:
                print(f"[Fetch VODs View] No VODs for {searched_channel_name} (User ID: {user_id}).")
                messages.info(request, f"Keine VODs für '{searched_channel_name}' gefunden.")
        else:
            print(f"[Fetch VODs View] Could not find User ID for: {searched_channel_name}")
            messages.error(request, f"Kanal '{searched_channel_name}' nicht gefunden oder API-Fehler.")

        request.session['twitch_vods_context'] = vod_results_for_session
        request.session['searched_channel_name_context'] = searched_channel_name
        request.session['search_attempted_context'] = search_attempted_for_session
    else:
        request.session['twitch_vods_context'] = []
        request.session['searched_channel_name_context'] = ''
        request.session['search_attempted_context'] = False
    return redirect('index')


@login_required
@require_POST
def import_selected_twitch_vod_view(request, vod_id):
    log_prefix_outer = f"[Import VOD View, VOD ID: {vod_id}] "
    vod_title = request.POST.get('vod_title', f"Twitch VOD {vod_id}")
    vod_url = request.POST.get('vod_url')
    twitch_channel_name = request.POST.get('twitch_channel_name', '').strip().lower()

    if not vod_url:
        messages.error(request, "VOD URL nicht übermittelt.");
        print(log_prefix_outer + "Error: VOD URL missing.");
        return redirect('index')
    if not twitch_channel_name:
        messages.error(request, "Twitch Kanalname fehlt.");
        print(log_prefix_outer + "Error: Channel name missing.");
        return redirect('index')

    print(
        log_prefix_outer + f"User '{request.user.username}' importing '{vod_title}' (URL: {vod_url}, Channel: {twitch_channel_name})")
    try:
        stream_obj = Stream.objects.create(
            user_id=request.user.username, stream_link=twitch_channel_name,
            stream_name=vod_title[:198], analysis_status='DOWNLOAD_SCHEDULED', twitch_vod_id=vod_id
        )
        stream_id_for_thread = stream_obj.id
        print(log_prefix_outer + f"Created Stream object ID {stream_id_for_thread}")
    except Exception as e_db_create:
        print(log_prefix_outer + f"ERROR creating Stream object: {e_db_create}");
        traceback.print_exc()
        messages.error(request, "DB-Fehler beim Erstellen des Stream-Eintrags.");
        return redirect('index')

    video_full_path_for_download = None
    try:
        user_id_part = str(request.user.username);
        stream_id_part = str(stream_id_for_thread)
        output_filename = f"{stream_id_for_thread}.mp4"
        relative_dir = os.path.join('uploads', user_id_part, stream_id_part)
        absolute_target_dir = os.path.join(settings.MEDIA_ROOT, relative_dir)
        os.makedirs(absolute_target_dir, exist_ok=True)
        video_full_path_for_download = os.path.join(absolute_target_dir, output_filename)
        relative_file_path_for_db = os.path.join(relative_dir, output_filename).replace('\\', '/')
        stream_obj.video_file.name = relative_file_path_for_db
        stream_obj.save(update_fields=['video_file'])
        print(log_prefix_outer + f"Target download path: {video_full_path_for_download}")
        print(log_prefix_outer + f"DB video_file.name set: {stream_obj.video_file.name}")
    except Exception as e_path:
        print(log_prefix_outer + f"ERROR creating download path: {e_path}");
        traceback.print_exc()
        messages.error(request, "Pfadfehler beim Vorbereiten des Imports.");
        stream_obj.delete();
        return redirect('index')

    try:
        print(log_prefix_outer + f"Starting download thread for Stream ID: {stream_id_for_thread}")
        download_analysis_thread = threading.Thread(
            target=run_vod_download_and_analysis_thread,
            args=(vod_url, video_full_path_for_download, stream_id_for_thread, request.user.username), daemon=True
        )
        download_analysis_thread.start()
        print(log_prefix_outer + "Download thread started.");
        messages.success(request, f"Import für '{vod_title}' gestartet.")
    except Exception as e_thread:
        print(log_prefix_outer + f"ERROR starting download thread: {e_thread}");
        traceback.print_exc()
        stream_obj.analysis_status = 'ERROR';
        stream_obj.save(update_fields=['analysis_status'])
        messages.error(request, "Fehler beim Starten des Importprozesses.")
    return redirect('index')


def run_vod_download_and_analysis_thread(vod_url, target_video_path, stream_id, user_name):
    thread_id = threading.get_ident()
    log_prefix = f"[VOD Download Thread-{thread_id}, StreamID: {stream_id}] "
    print(f"\n{log_prefix}--- Thread started for VOD: {vod_url}, Target: {target_video_path} ---")
    start_time_thread = time.time();
    stream_obj = None;
    download_successful = False;
    process = None;
    repack_process = None

    try:
        stream_obj = Stream.objects.get(id=stream_id)
        stream_obj.analysis_status = 'DOWNLOADING';
        stream_obj.save(update_fields=['analysis_status'])

        streamlink_path = getattr(settings, 'STREAMLINK_PATH', 'streamlink')
        ffmpeg_path = getattr(settings, 'FFMPEG_PATH', 'ffmpeg')
        download_quality = "480p"

        streamlink_cmd = [streamlink_path, "--ffmpeg-ffmpeg", ffmpeg_path, "--twitch-disable-ads", "--force", vod_url,
                          download_quality, "-o", target_video_path]
        print(log_prefix + f"Executing Streamlink (Quality '{download_quality}'): {' '.join(streamlink_cmd)}")
        process = subprocess.Popen(streamlink_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   encoding='utf-8')
        stdout, stderr = process.communicate(timeout=getattr(settings, 'STREAMLINK_TIMEOUT', 10800))

        if process.returncode == 0 and os.path.exists(target_video_path) and os.path.getsize(target_video_path) > 1000:
            print(log_prefix + f"Streamlink download successful: {target_video_path}.")
            original_extension = os.path.splitext(target_video_path)[1].lower() or ".mp4"
            repacked_video_path = target_video_path + ".repacked_vod" + original_extension
            ffmpeg_repack_cmd = [ffmpeg_path, '-i', target_video_path, '-c', 'copy', '-movflags', '+faststart',
                                 repacked_video_path]
            print(log_prefix + f"Executing FFmpeg repack: {' '.join(ffmpeg_repack_cmd)}")
            try:
                repack_process = subprocess.Popen(ffmpeg_repack_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                  text=True, encoding='utf-8')
                s_repack, e_repack = repack_process.communicate(timeout=getattr(settings, 'FFMPEG_REPACK_TIMEOUT', 600))
                if repack_process.returncode == 0:
                    print(log_prefix + f"Repacked to {repacked_video_path}.")
                    shutil.move(repacked_video_path, target_video_path)  # Überschreibt Original
                    print(log_prefix + f"Original replaced with repacked: {target_video_path}")
                    download_successful = True;
                    stream_obj.analysis_status = 'DOWNLOAD_COMPLETE'
                else:
                    print(log_prefix + f"FFmpeg repack FAILED. RC: {repack_process.returncode}");
                    print(f"  Stderr: {e_repack.strip() if e_repack else 'N/A'}")
                    if os.path.exists(repacked_video_path): os.remove(repacked_video_path)
                    download_successful = True;
                    stream_obj.analysis_status = 'DOWNLOAD_COMPLETE';
                    print(log_prefix + "WARN: Repack failed, using original.")
            except Exception as e_repack:  # Inkl. Timeout, FileNotFoundError für ffmpeg
                print(log_prefix + f"ERROR during FFmpeg repack: {e_repack}");
                traceback.print_exc()
                if repack_process and repack_process.poll() is None: repack_process.kill(); repack_process.communicate()
                if os.path.exists(repacked_video_path): os.remove(repacked_video_path)
                download_successful = True;
                stream_obj.analysis_status = 'DOWNLOAD_COMPLETE';
                print(log_prefix + "WARN: Repack exception, using original.")
        else:
            print(
                log_prefix + f"Streamlink download FAILED or file invalid. RC: {process.returncode if process else 'N/A'}");
            if stderr: print(f"  Streamlink stderr: {stderr.strip()}")
            stream_obj.analysis_status = 'ERROR_DOWNLOAD'

        if stream_obj: stream_obj.save(update_fields=['analysis_status'])
    except Exception as e_dl_outer:
        print(log_prefix + f"CRITICAL ERROR in VOD download/repack: {e_dl_outer}");
        traceback.print_exc()
        if stream_obj: stream_obj.analysis_status = 'ERROR_DOWNLOAD'; stream_obj.save(update_fields=['analysis_status'])
        download_successful = False

    if download_successful:
        print(log_prefix + f"Download/repack complete. No analysis started (manual processing expected).")
    else:
        print(log_prefix + "Download failed/incomplete. Analysis skipped.")

    end_time_thread = time.time()
    print(
        f"{log_prefix}--- Thread finished (Duration: {time.strftime('%H:%M:%S', time.gmtime(end_time_thread - start_time_thread))}) ---\n")