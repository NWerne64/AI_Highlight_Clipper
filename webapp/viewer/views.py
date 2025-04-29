# AI_Highlight_Clipper/webapp/viewer/views.py

from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse, HttpResponseRedirect
from django.urls import reverse
from django.contrib import messages # Wird jetzt nur noch für Errors genutzt
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login, authenticate
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.conf import settings
import os
import threading
import traceback
import numpy as np

from .models import Stream, StreamHighlight
from .forms import StreamUploadForm
from .analysis import run_analysis_and_extraction_thread, find_highlights_by_loudness, LOUDNESS_THRESHOLD

# --- INDEX / LOGIN / HAUPTSEITE ---
def index(request):
    reg_form = UserCreationForm()
    if request.method == 'POST':
        if 'login_submit' in request.POST:
            username_post = request.POST.get('username')
            password_post = request.POST.get('password')
            user_auth = authenticate(request, username=username_post, password=password_post)
            if user_auth is not None: login(request, user_auth); return redirect('index')
            else: return render(request, 'viewer/index.html', {'form': reg_form, 'login_error': True})
        elif 'register_submit' in request.POST:
            reg_form_posted = UserCreationForm(request.POST)
            if reg_form_posted.is_valid():
                user = reg_form_posted.save(); login(request, user)
                print(f"Neuer User registriert: {user.username}"); return redirect('index')
            else:
                print(f"Registrierungsfehler: {reg_form_posted.errors.as_json()}")
                return render(request, 'viewer/index.html', {'form': reg_form_posted })
    if request.user.is_authenticated:
        stream_data = Stream.objects.filter(user_id=request.user.username).order_by('-id')
        upload_form = StreamUploadForm()
        response_data = { "stream_data": stream_data, "name": request.user.username, "is_staff": request.user.is_staff, "upload_form": upload_form }
        return render(request, 'viewer/main.html', response_data)
    else: return render(request, 'viewer/index.html', {'form': reg_form})

# --- STREAM/VIDEO HOCHLADEN + Thread Start ---
@login_required
def add_stream(request):
    if request.method == 'POST':
        form = StreamUploadForm(request.POST, request.FILES)
        if form.is_valid():
            print("Upload Form is valid. Saving stream object...")
            new_stream = form.save(commit=False); new_stream.user_id = request.user.username
            if new_stream.video_file and not new_stream.stream_name: new_stream.stream_name = os.path.splitext(new_stream.video_file.name)[0]
            new_stream.analysis_status = 'PENDING'; new_stream.save()
            print(f"Video '{new_stream.video_file.name}' saved. Stream ID: {new_stream.id}, Status: PENDING")
            try:
                if new_stream.video_file and hasattr(new_stream.video_file, 'path') and os.path.exists(new_stream.video_file.path):
                    video_full_path = new_stream.video_file.path; stream_id_for_thread = new_stream.id; user_name_for_thread = request.user.username
                    print(f"Starting analysis thread for Stream ID: {stream_id_for_thread}")
                    analysis_thread = threading.Thread(target=run_analysis_and_extraction_thread, args=(video_full_path, stream_id_for_thread, user_name_for_thread), daemon=True )
                    analysis_thread.start()
                    print(f"Analysis thread started. View is returning.")
                    # messages.success(request, f"Video '{new_stream.stream_name}' hochgeladen. Analyse gestartet.") # <-- DIESE ZEILE ENTFERNT/AUSKOMMENTIERT
                else:
                     print(f"ERROR: Video file path not found. Analysis not started."); new_stream.analysis_status = 'ERROR'; new_stream.save(update_fields=['analysis_status'])
                     messages.error(request, "Fehler: Videodatei nicht gefunden, Analyse nicht gestartet.") # Info an User
            except Exception as e_thread:
                print(f"ERROR starting analysis thread: {e_thread}"); traceback.print_exc()
                try: new_stream.analysis_status = 'ERROR'; new_stream.save(update_fields=['analysis_status'])
                except Exception as e_save: print(f"Could not save ERROR status: {e_save}")
                messages.error(request, "Fehler beim Starten der Analyse.") # Info an User

            return redirect('index') # Immer zurück zur Index-Seite
        else:
             stream_data = Stream.objects.filter(user_id=request.user.username).order_by('-id')
             response_data = { "stream_data": stream_data, "name": request.user.username, "is_staff": request.user.is_staff, "upload_form": form }
             messages.error(request, "Fehler beim Hochladen. Formular prüfen.") # Fehler anzeigen
             return render(request, 'viewer/main.html', response_data)
    else: return redirect('index')

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

# --- STREAM LÖSCHEN ---
@login_required
@require_POST
def delete_stream(request, stream_id):
    # ... (Code bleibt wie zuletzt) ...
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    stream_title_for_log = stream_obj.stream_name or stream_obj.stream_link
    print(f"Attempting delete Stream ID: {stream_id} ('{stream_title_for_log}') for User: {request.user.username}")
    video_full_path, csv_full_path, highlight_files_to_delete, stream_dir = None, None, [], None
    if stream_obj.video_file and hasattr(stream_obj.video_file, 'path'): video_full_path = stream_obj.video_file.path; stream_dir = os.path.dirname(video_full_path)
    if stream_obj.sound_csv_path:
        if os.path.isabs(stream_obj.sound_csv_path): csv_full_path = stream_obj.sound_csv_path
        elif settings.MEDIA_ROOT: csv_full_path = os.path.join(settings.MEDIA_ROOT, stream_obj.sound_csv_path)
        if csv_full_path and not os.path.exists(csv_full_path): print(f"Warning: CSV not found at {csv_full_path}"); csv_full_path = None
    highlights = StreamHighlight.objects.filter(user_id=request.user.username, stream_link=stream_obj.stream_link)
    for hl in highlights:
        try:
            if hl.clip_link and settings.MEDIA_ROOT: highlight_files_to_delete.append(os.path.join(settings.MEDIA_ROOT, hl.clip_link))
        except Exception as e_path: print(f"Warning: Path construction failed for clip {hl.clip_link}: {e_path}")
    print(f"Deleting {highlights.count()} highlight DB entries..."); highlights.delete()
    print(f"Deleting Stream DB entry (ID: {stream_id})..."); stream_obj.delete()
    print(f"Deleting {len(highlight_files_to_delete)} associated highlight files...")
    for file_path in highlight_files_to_delete:
        if file_path and os.path.exists(file_path):
            try: os.remove(file_path); print(f"  Deleted: {file_path}")
            except OSError as e_del_clip: print(f"  Error deleting {file_path}: {e_del_clip}")
        else: print(f"  File not found/path invalid: {file_path}")
    if csv_full_path and os.path.exists(csv_full_path):
        try: os.remove(csv_full_path); print(f"Deleted analysis CSV: {csv_full_path}")
        except OSError as e_del_csv: print(f"Error deleting analysis CSV {csv_full_path}: {e_del_csv}")
    if video_full_path and os.path.exists(video_full_path):
        try: os.remove(video_full_path); print(f"Deleted video file: {video_full_path}")
        except OSError as e_del_vid: print(f"Error deleting video {video_full_path}: {e_del_vid}")
    if stream_dir and os.path.exists(stream_dir):
        try:
            if not os.listdir(stream_dir): os.rmdir(stream_dir); print(f"Deleted empty stream directory: {stream_dir}")
            else: print(f"Stream directory not empty: {stream_dir}")
        except OSError as e_rmdir: print(f"Error deleting dir {stream_dir}: {e_rmdir}")
    print(f"Deletion process completed for former Stream ID: {stream_id}")
    messages.success(request, f"Stream '{stream_title_for_log}' wurde gelöscht.")
    return redirect('index')

# --- VIDEOPLAYER SEITE ---
@login_required
def video_player_view(request, stream_id):
    # ... (Code bleibt wie zuletzt) ...
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    context = { 'stream': stream_obj, 'name': request.user.username, 'is_staff': request.user.is_staff, }
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
    new_threshold_str = request.POST.get('new_threshold')
    new_threshold = None
    try:
        new_threshold = float(new_threshold_str)
        if not (0.0001 < new_threshold < 10.0): raise ValueError("Threshold value out of range")
        print(f"Using user-provided threshold: {new_threshold}")
    except (ValueError, TypeError):
        print(f"ERROR: Invalid threshold: '{new_threshold_str}'. Using default.")
        messages.error(request, f"Ungültiger Threshold-Wert '{new_threshold_str}'.")
        return redirect('generator', stream_id=stream_id)
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
    except Exception as e_regen:
         print(f"ERROR during highlight regeneration: {e_regen}"); traceback.print_exc()
         messages.error(request, "Fehler bei der Neugenerierung der Highlights.")
    return redirect('generator', stream_id=stream_id)