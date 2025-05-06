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
import os
import threading
import traceback
import numpy as np
import subprocess # Für Popen und taskkill
import sys
import uuid
import shutil # Für Verzeichnislöschung

from .models import Stream, StreamHighlight
from .forms import StreamUploadForm
from .analysis import run_analysis_and_extraction_thread, find_highlights_by_loudness, LOUDNESS_THRESHOLD

# --- INDEX / LOGIN / HAUPTSEITE ---
def index(request):
    reg_form = UserCreationForm()
    if request.method == 'POST':
        if 'login_submit' in request.POST:
            username_post = request.POST.get('username'); password_post = request.POST.get('password')
            user_auth = authenticate(request, username=username_post, password=password_post)
            if user_auth is not None: login(request, user_auth); return redirect('index')
            else: return render(request, 'viewer/index.html', {'form': reg_form, 'login_error': True})
        elif 'register_submit' in request.POST:
            reg_form_posted = UserCreationForm(request.POST)
            if reg_form_posted.is_valid():
                user = reg_form_posted.save(); login(request, user); print(f"Neuer User: {user.username}"); return redirect('index')
            else: print(f"Reg.-Fehler: {reg_form_posted.errors.as_json()}"); return render(request, 'viewer/index.html', {'form': reg_form_posted })
    if request.user.is_authenticated:
        stream_data = Stream.objects.filter(user_id=request.user.username).order_by('-id')
        upload_form = StreamUploadForm()
        response_data = { "stream_data": stream_data, "name": request.user.username, "is_staff": request.user.is_staff, "upload_form": upload_form }
        return render(request, 'viewer/main.html', response_data)
    else: return render(request, 'viewer/index.html', {'form': reg_form})

# --- STREAM/VIDEO HOCHLADEN + Thread Start ---
@login_required
def add_stream(request):
    # ... (Code bleibt wie zuletzt) ...
    if request.method == 'POST':
        form = StreamUploadForm(request.POST, request.FILES)
        if form.is_valid():
            if not request.FILES.get('video_file'): messages.error(request, "Bitte Videodatei auswählen."); return render(request, 'viewer/main.html', {'upload_form': form})
            print("Upload Form valid & file present...")
            new_stream = form.save(commit=False); new_stream.user_id = request.user.username
            if not new_stream.stream_name: new_stream.stream_name = os.path.splitext(new_stream.video_file.name)[0]
            new_stream.analysis_status = 'PENDING'; new_stream.save() # Speichern MIT Datei
            print(f"Video '{new_stream.video_file.name}' saved. Stream ID: {new_stream.id}, Status: PENDING")
            try:
                if hasattr(new_stream.video_file, 'path') and new_stream.video_file.path and os.path.exists(new_stream.video_file.path):
                    video_full_path = new_stream.video_file.path; stream_id_for_thread = new_stream.id; user_name_for_thread = request.user.username
                    print(f"Starting analysis thread for Stream ID: {stream_id_for_thread}")
                    analysis_thread = threading.Thread(target=run_analysis_and_extraction_thread, args=(video_full_path, stream_id_for_thread, user_name_for_thread), daemon=True )
                    analysis_thread.start()
                    print(f"Analysis thread started. View is returning.")
                    # Status wird im Thread gesetzt
                else:
                     print(f"ERROR: Video file path not found after save ('{getattr(new_stream.video_file, 'path', 'N/A')}'). Analysis not started."); new_stream.analysis_status = 'ERROR'; new_stream.save(update_fields=['analysis_status'])
                     messages.error(request, "Fehler: Videodatei nicht gefunden/verarbeitet nach Speichern.")
            except Exception as e_thread:
                print(f"ERROR starting analysis thread: {e_thread}"); traceback.print_exc()
                try: new_stream.analysis_status = 'ERROR'; new_stream.save(update_fields=['analysis_status'])
                except Exception as e_save: print(f"Could not save ERROR status: {e_save}")
                messages.error(request, "Fehler beim Starten der Analyse.")
            return redirect('index')
        else:
             stream_data = Stream.objects.filter(user_id=request.user.username).order_by('-id')
             response_data = { "stream_data": stream_data, "name": request.user.username, "is_staff": request.user.is_staff, "upload_form": form }
             messages.error(request, "Fehler beim Hochladen. Formular prüfen.")
             return render(request, 'viewer/main.html', response_data)
    else: return redirect('index')

# --- TWITCH STREAM AUFNAHME STARTEN (Speichert PID & video_file.name) ---
@login_required
def record_stream_view(request):
    if request.method == 'POST':
        twitch_username = request.POST.get('twitch_username', '').strip()
        quality = request.POST.get('quality', '480p')
        if not twitch_username: messages.error(request, "Bitte Kanalnamen eingeben."); return redirect('index')
        client_id = getattr(settings, 'TWITCH_CLIENT_ID', None); client_secret = getattr(settings, 'TWITCH_CLIENT_SECRET', None); recorder_script_path = getattr(settings, 'TWITCH_RECORDER_SCRIPT_PATH', None)
        if not all([client_id, client_secret, recorder_script_path]) or not os.path.exists(recorder_script_path): messages.error(request, "Fehler: Konfiguration/Skript fehlt."); return redirect('index')

        # 1. Stream-Objekt erstellen
        try:
            stream_obj = Stream.objects.create(user_id=request.user.username, stream_link=twitch_username, stream_name=f"Aufnahme: {twitch_username}", analysis_status='RECORDING_SCHEDULED')
            stream_id = stream_obj.id; print(f"Created Stream object ID {stream_id} for recording '{twitch_username}'")
        except Exception as e_db_create: print(f"ERROR creating Stream object: {e_db_create}"); messages.error(request, "DB Fehler."); return redirect('index')

        # 2. Zielpfad bestimmen & in DB speichern
        try:
            user_id_part = str(request.user.username); stream_id_part = str(stream_id); output_filename = f"{stream_id}.mp4"
            relative_dir = os.path.join('uploads', user_id_part, stream_id_part); absolute_dir = os.path.join(settings.MEDIA_ROOT, relative_dir); os.makedirs(absolute_dir, exist_ok=True)
            output_full_path = os.path.join(absolute_dir, output_filename)
            # Speichere den relativen Pfad, den die Datei haben WIRD
            stream_obj.video_file.name = os.path.join(relative_dir, output_filename).replace('\\', '/')
            print(f"Target recording path: {output_full_path}"); print(f"Setting video_file.name to: {stream_obj.video_file.name}")
            # Speichere video_file.name VOR dem Start
            stream_obj.save(update_fields=['video_file'])
        except Exception as e_path: print(f"ERROR creating recording path: {e_path}"); messages.error(request, "Pfadfehler."); stream_obj.delete(); return redirect('index')

        # 3. Recorder-Skript starten
        command = [ sys.executable, recorder_script_path, '--username', twitch_username, '--quality', quality, '--uid', str(stream_id), '--output-path', output_full_path, '--client-id', client_id, '--client-secret', client_secret ]
        print(f"Starting background recorder process for Stream ID {stream_id}:"); print(f"Command: {' '.join(command)}")
        try:
            process = subprocess.Popen(command)
            stream_obj.recorder_pid = process.pid; stream_obj.analysis_status = 'RECORDING'; stream_obj.save(update_fields=['recorder_pid', 'analysis_status'])
            print(f"Recorder process started PID: {process.pid}. DB updated.")
            messages.success(request, f"Aufnahme für '{twitch_username}' (ID: {stream_id}) gestartet (PID: {process.pid}).")
        except Exception as e_popen:
            print(f"ERROR starting recorder process: {e_popen}"); traceback.print_exc(); messages.error(request, "Fehler Start Aufnahme-Prozess.")
            stream_obj.analysis_status = 'ERROR'; stream_obj.recorder_pid = None; stream_obj.save(update_fields=['analysis_status', 'recorder_pid'])
        return redirect('index')
    else: messages.warning(request, "Ungültige Anfrage."); return redirect('index')

# --- NEUE VIEW: TWITCH AUFNAHME STOPPEN ---
@login_required
@require_POST # Sicherstellen, dass nur POST-Requests hier ankommen
def stop_recording_view(request, stream_id):
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    pid_to_kill = stream_obj.recorder_pid
    log_prefix = f"[Stop Request View Stream {stream_id}] "

    print(log_prefix + f"Attempting to stop recording (PID: {pid_to_kill})")

    if pid_to_kill:
        killed = False
        try:
            # Taskkill für Windows mit /T um Kindprozesse (streamlink) zu beenden
            kill_command = ['taskkill', '/F', '/PID', str(pid_to_kill), '/T']
            # Führe aus und fange Output ab, prüfe Returncode
            result = subprocess.run(kill_command, capture_output=True, text=True, check=False, encoding='utf-8')

            if result.returncode == 0:
                print(log_prefix + f"Successfully sent kill signal to PID {pid_to_kill}.")
                messages.success(request, f"Aufnahme für Stream {stream_id} gestoppt.")
                stream_obj.analysis_status = 'MANUALLY_STOPPED' # Neuer Status setzen
                killed = True
            # Code 128 = Process not found (Windows)
            elif result.returncode == 128 or "nicht gefunden" in result.stderr.lower():
                 print(log_prefix + f"Process with PID {pid_to_kill} was not found (already stopped?).")
                 messages.warning(request, f"Aufnahme für Stream {stream_id} war bereits beendet oder konnte nicht gefunden werden.")
                 # Status trotzdem setzen, falls er noch auf RECORDING stand
                 if stream_obj.analysis_status == 'RECORDING':
                      stream_obj.analysis_status = 'MANUALLY_STOPPED'
                 killed = True # Zählt als "erfolgreich gestoppt"
            else: # Anderer Fehler von taskkill
                 print(log_prefix + f"Error stopping process PID {pid_to_kill}. RC: {result.returncode}")
                 if result.stderr: print(log_prefix + f"taskkill stderr: {result.stderr.strip()}")
                 if result.stdout: print(log_prefix + f"taskkill stdout: {result.stdout.strip()}")
                 messages.error(request, f"Fehler beim Stoppen der Aufnahme (PID: {pid_to_kill}).")
                 stream_obj.analysis_status = 'ERROR' # Fehlerstatus

        except FileNotFoundError:
             print(log_prefix + "ERROR: taskkill command not found. Cannot stop process.")
             messages.error(request, "Fehler: 'taskkill' nicht gefunden. Aufnahme konnte nicht gestoppt werden.")
             stream_obj.analysis_status = 'ERROR'
        except Exception as e_kill:
            print(log_prefix + f"Unexpected error stopping PID {pid_to_kill}: {e_kill}")
            traceback.print_exc()
            messages.error(request, f"Unerwarteter Fehler beim Stoppen der Aufnahme für Stream {stream_id}.")
            stream_obj.analysis_status = 'ERROR'

        # PID aus DB entfernen und Status speichern, egal ob erfolgreich oder nicht
        stream_obj.recorder_pid = None
        # Setze Status auf PENDING, damit Analyse ggf. gestartet werden kann,
        # ausser es gab einen Fehler beim Stoppen selbst.
        if stream_obj.analysis_status not in ['ERROR', 'MANUALLY_STOPPED']:
             stream_obj.analysis_status = 'PENDING' # Bereit für Analyse
        stream_obj.save(update_fields=['analysis_status', 'recorder_pid'])

    else: # Keine PID in DB gefunden
        print(log_prefix + f"No PID found in database for Stream ID: {stream_id}. Cannot stop.")
        messages.warning(request, f"Keine laufende Aufnahme-PID für Stream {stream_id} gefunden.")
        # Status korrigieren, falls er fälschlich auf RECORDING steht
        if stream_obj.analysis_status == 'RECORDING':
             stream_obj.analysis_status = 'ERROR' # Unbekannter Zustand
             stream_obj.recorder_pid = None
             stream_obj.save(update_fields=['analysis_status', 'recorder_pid'])

    # Immer zur Hauptseite zurückleiten
    return redirect('index')

# --- CLIP HINZUFÜGEN ---
def add_clip(request): print("WARNUNG: /add_clip/ aufgerufen."); return JsonResponse({'status': 'add_clip endpoint reached (likely unused)'})

# --- STREAM DETAILSEITE (Highlights) ---
@login_required
def stream(request, stream_id):
    # ... (Code bleibt wie zuletzt) ...
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    clips_data = StreamHighlight.objects.filter(user_id=request.user.username, stream_link=stream_obj.stream_link).order_by('id')
    for clip in clips_data:
         if clip.clip_link and not clip.clip_link.startswith(('http://', 'https://', '/')):
             try: clip.media_url = settings.MEDIA_URL + clip.clip_link.replace('\\','/')
             except Exception: clip.media_url = None
         else: clip.media_url = clip.clip_link
    response_data = { "name": request.user.username, "stream": stream_obj, "is_staff": request.user.is_staff, "clips_data": clips_data }
    return render(request, 'viewer/stream.html', response_data)

# --- STREAM LÖSCHEN (VERBESSERT) ---
@login_required
@require_POST
def delete_stream(request, stream_id):
    # ... (Code bleibt wie zuletzt, nutzt shutil.rmtree) ...
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    stream_title_for_log = stream_obj.stream_name or stream_obj.stream_link
    print(f"Attempting delete Stream ID: {stream_id} ('{stream_title_for_log}') for User: {request.user.username}")
    stream_dir_absolute = None
    if settings.MEDIA_ROOT and stream_obj.user_id and stream_obj.id:
        stream_dir_relative = os.path.join('uploads', str(request.user.username), str(stream_obj.id))
        stream_dir_absolute = os.path.join(settings.MEDIA_ROOT, stream_dir_relative)
        print(f"Determined stream directory for deletion: {stream_dir_absolute}")
    highlights = StreamHighlight.objects.filter(user_id=request.user.username, stream_link=stream_obj.stream_link)
    print(f"Deleting {highlights.count()} highlight DB entries..."); highlights.delete()
    print(f"Deleting Stream DB entry (ID: {stream_id})..."); stream_obj.delete()
    if stream_dir_absolute and os.path.exists(stream_dir_absolute):
        try:
            shutil.rmtree(stream_dir_absolute); print(f"Deleted stream directory: {stream_dir_absolute}")
            messages.success(request, f"Stream '{stream_title_for_log}' und Dateien gelöscht.")
        except OSError as e_rmdir: print(f"Error deleting dir {stream_dir_absolute}: {e_rmdir}"); messages.error(request, f"Fehler: Stream-Dateien konnten nicht gelöscht werden.")
    else: print(f"Stream directory not found: {stream_dir_absolute}"); messages.warning(request, f"Stream '{stream_title_for_log}' aus DB gelöscht (Dateien nicht gefunden).")
    print(f"Deletion process completed for former Stream ID: {stream_id}")
    return redirect('index')

# --- VIDEOPLAYER SEITE (ANGEPASST) ---
@login_required
def video_player_view(request, stream_id):
    # ... (Code bleibt wie zuletzt, prüft Existenz) ...
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    video_url, video_exists = None, False
    if stream_obj.video_file and stream_obj.video_file.name:
        try:
            if stream_obj.video_file.storage.exists(stream_obj.video_file.name):
                 video_url = stream_obj.video_file.url; video_exists = True; print(f"Found video URL via FileField: {video_url}")
            else: print(f"Warning: FileField set but file not found: {stream_obj.video_file.name}")
        except Exception as e: print(f"Error getting video_file.url: {e}")
    if not video_exists: # Fallback für Aufnahmen, deren Feld nicht gesetzt wurde oder deren Datei verschoben wurde?
        expected_relative_path = os.path.join('uploads', str(request.user.username), str(stream_id), f"{stream_id}.mp4").replace('\\', '/')
        expected_full_path = os.path.join(settings.MEDIA_ROOT, expected_relative_path)
        print(f"Checking expected path: {expected_full_path}")
        if os.path.exists(expected_full_path):
            print("File exists at expected path. Constructing URL."); video_url = settings.MEDIA_URL + expected_relative_path; video_exists = True
            # Optional: DB-Feld aktualisieren
            # if not stream_obj.video_file.name: stream_obj.video_file.name = expected_relative_path; stream_obj.save(update_fields=['video_file'])
        else: print("File does not exist at expected path.")
    context = { 'stream': stream_obj, 'name': request.user.username, 'is_staff': request.user.is_staff, 'video_url': video_url, 'video_exists': video_exists }
    return render(request, 'viewer/video_player.html', context)

# --- GENERATOR SEITE ---
@login_required
def generator_view(request, stream_id):
    # ... (Code bleibt wie zuletzt) ...
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    clips_data = StreamHighlight.objects.filter(user_id=request.user.username, stream_link=stream_obj.stream_link).order_by('id')
    for clip in clips_data:
         if clip.clip_link and not clip.clip_link.startswith(('http://', 'https://', '/')):
             try: clip.media_url = settings.MEDIA_URL + clip.clip_link.replace('\\','/')
             except Exception: clip.media_url = None
         else: clip.media_url = clip.clip_link
    context = { 'stream': stream_obj, 'name': request.user.username, 'is_staff': request.user.is_staff, 'clips_data': clips_data, 'current_threshold': LOUDNESS_THRESHOLD }
    return render(request, 'viewer/generator.html', context)

# --- HIGHLIGHTS NEU GENERIEREN ---
@login_required
@require_POST
def regenerate_highlights_view(request, stream_id):
    # ... (Code bleibt wie zuletzt) ...
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    print(f"--- Received POST request to regenerate highlights for Stream ID: {stream_id} ---")
    new_threshold_str = request.POST.get('new_threshold'); new_threshold = None
    try:
        new_threshold = float(new_threshold_str);
        if not (0.0001 < new_threshold < 10.0): raise ValueError("Threshold out of range")
        print(f"Using user-provided threshold: {new_threshold}")
    except (ValueError, TypeError): print(f"ERROR: Invalid threshold: '{new_threshold_str}'."); messages.error(request, f"Ungültiger Threshold-Wert '{new_threshold_str}'."); return redirect('generator', stream_id=stream_id)
    sound_csv_path = None
    if stream_obj.sound_csv_path:
        potential_path = os.path.join(settings.MEDIA_ROOT, stream_obj.sound_csv_path) if settings.MEDIA_ROOT and not os.path.isabs(stream_obj.sound_csv_path) else stream_obj.sound_csv_path
        if os.path.exists(potential_path): sound_csv_path = potential_path
        else: print(f"WARNUNG: CSV Pfad '{stream_obj.sound_csv_path}' nicht gefunden unter '{potential_path}'")
    video_path = None
    if stream_obj.video_file and hasattr(stream_obj.video_file, 'path') and os.path.exists(stream_obj.video_file.path): video_path = stream_obj.video_file.path
    if not video_path: messages.error(request, "Original Video nicht gefunden."); return redirect('generator', stream_id=stream_id)
    if not sound_csv_path: messages.error(request, "Analyse-Daten (CSV) nicht gefunden."); return redirect('generator', stream_id=stream_id)
    print(f"Regenerating clips using threshold: {new_threshold}")
    try:
        find_highlights_by_loudness( sound_csv_path=sound_csv_path, video_path=video_path, stream_id=stream_id, user_name=request.user.username, threshold=new_threshold )
        messages.success(request, f"Highlights wurden mit Threshold {new_threshold:.4f} neu generiert.")
        print(f"--- Finished regenerating highlights for Stream ID: {stream_id} ---")
    except Exception as e_regen: print(f"ERROR during highlight regeneration: {e_regen}"); traceback.print_exc(); messages.error(request, "Fehler bei der Neugenerierung der Highlights.")
    return redirect('generator', stream_id=stream_id)