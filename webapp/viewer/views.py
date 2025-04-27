# AI_Highlight_Clipper/webapp/viewer/views.py

from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse, HttpResponseRedirect
from django.urls import reverse
from django.contrib import messages # Für Feedback an User
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login, authenticate
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.conf import settings
import os
import threading
import traceback

from .models import Stream, StreamHighlight
from .forms import StreamUploadForm
# Importiere BEIDE Analyse-Funktionen und den default Threshold
from .analysis import run_analysis_and_extraction_thread, find_highlights_by_loudness, LOUDNESS_THRESHOLD

# --- INDEX / LOGIN / HAUPTSEITE ---
def index(request):
    reg_form = UserCreationForm() # Immer ein leeres Formular für GET bereitstellen

    if request.method == 'POST':
        # Prüfen, welcher Button geklickt wurde
        if 'login_submit' in request.POST:
            username_post = request.POST.get('username')
            password_post = request.POST.get('password')
            user_auth = authenticate(request, username=username_post, password=password_post)
            if user_auth is not None:
                login(request, user_auth)
                return redirect('index')
            else:
                # Zeige Login-Seite erneut mit Fehlermeldung
                return render(request, 'viewer/index.html', {'form': reg_form, 'login_error': True})

        elif 'register_submit' in request.POST:
            reg_form_posted = UserCreationForm(request.POST)
            if reg_form_posted.is_valid():
                user = reg_form_posted.save()
                login(request, user)
                print(f"Neuer User registriert und eingeloggt: {user.username}")
                return redirect('index')
            else:
                # Zeige Login/Registrierungs-Seite erneut mit fehlerhaftem Registrierungsformular
                print(f"Registrierungsfehler: {reg_form_posted.errors.as_json()}")
                return render(request, 'viewer/index.html', {'form': reg_form_posted }) # Fehlerhaftes Formular anzeigen

    # --- GET Request ---
    if request.user.is_authenticated:
        # Eingeloggt: Zeige Hauptseite
        stream_data = Stream.objects.filter(user_id=request.user.username).order_by('-id')
        upload_form = StreamUploadForm()
        response_data = { "stream_data": stream_data, "name": request.user.username, "is_staff": request.user.is_staff, "upload_form": upload_form }
        return render(request, 'viewer/main.html', response_data)
    else:
        # Nicht eingeloggt: Zeige Login/Registrierungsseite
        return render(request, 'viewer/index.html', {'form': reg_form})


# --- STREAM/VIDEO HOCHLADEN + Thread Start ---
@login_required
def add_stream(request):
    if request.method == 'POST':
        form = StreamUploadForm(request.POST, request.FILES)
        if form.is_valid():
            print("Upload Form is valid. Saving stream object...")
            new_stream = form.save(commit=False)
            new_stream.user_id = request.user.username
            if new_stream.video_file and not new_stream.stream_name:
                 new_stream.stream_name = os.path.splitext(new_stream.video_file.name)[0]
            # Status vor dem Speichern setzen
            new_stream.analysis_status = 'PENDING'
            # Speichern, um ID zu bekommen (wichtig, falls get_upload_path ID nutzt)
            new_stream.save()
            # Erneutes Speichern, falls get_upload_path die ID verwendet und der Pfad sich ändert
            # (Wenn get_upload_path die ID *nicht* nutzt, ist das zweite save() nicht nötig)
            # form.save() # Könnte man hier nochmal aufrufen, um die Datei sicher zu speichern

            print(f"Video '{new_stream.video_file.name}' saved for user {new_stream.user_id} at path: {new_stream.video_file.path}")
            print(f"Stream object created with ID: {new_stream.id}, Status: PENDING")

            # Analyse im Hintergrund starten
            try:
                if new_stream.video_file and hasattr(new_stream.video_file, 'path') and os.path.exists(new_stream.video_file.path):
                    video_full_path = new_stream.video_file.path
                    stream_id_for_thread = new_stream.id
                    user_name_for_thread = request.user.username

                    print(f"Starting analysis thread for Stream ID: {stream_id_for_thread}")
                    analysis_thread = threading.Thread(
                        target=run_analysis_and_extraction_thread,
                        args=(video_full_path, stream_id_for_thread, user_name_for_thread),
                        daemon=True
                    )
                    analysis_thread.start()
                    print(f"Analysis thread started for Stream ID {stream_id_for_thread}. View is returning.")
                    # Status direkt nach Thread-Start auf PROCESSING setzen
                    # (Machen wir jetzt im Thread selbst, um DB-Zugriffe zu bündeln)
                    # new_stream.analysis_status = 'PROCESSING'
                    # new_stream.save(update_fields=['analysis_status'])
                else:
                     print(f"ERROR: Video file path not found or file does not exist for Stream ID {new_stream.id}. Analysis thread not started.")
                     # Setze Fehlerstatus direkt
                     new_stream.analysis_status = 'ERROR'
                     new_stream.save(update_fields=['analysis_status'])

            except Exception as e_thread:
                print(f"ERROR starting analysis thread for Stream ID {new_stream.id}: {e_thread}")
                traceback.print_exc()
                try: # Versuche Fehlerstatus zu speichern
                     new_stream.analysis_status = 'ERROR'
                     new_stream.save(update_fields=['analysis_status'])
                except Exception as e_save: print(f"Could not save ERROR status: {e_save}")

            messages.success(request, f"Video '{new_stream.stream_name}' hochgeladen. Analyse gestartet.")
            return redirect('index')
        else:
             # Formular ungültig
             stream_data = Stream.objects.filter(user_id=request.user.username).order_by('-id')
             # Füge das fehlerhafte Formular zum Kontext hinzu, um Fehler anzuzeigen
             response_data = { "stream_data": stream_data, "name": request.user.username, "is_staff": request.user.is_staff, "upload_form": form }
             messages.error(request, "Fehler beim Hochladen des Videos. Bitte Formular prüfen.")
             return render(request, 'viewer/main.html', response_data)
    else:
        # GET Request nicht unterstützt
        return redirect('index')

# --- CLIP HINZUFÜGEN ---
def add_clip(request):
    # ... (bleibt wie es war, wird nicht mehr aktiv genutzt) ...
    print("WARNUNG: /add_clip/ Endpunkt aufgerufen.")
    return JsonResponse({'status': 'add_clip endpoint reached (likely unused)'})

# --- STREAM DETAILSEITE (Highlights) ---
@login_required
def stream(request, stream_id):
    # ... (bleibt wie es war, inkl. media_url Erstellung) ...
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    clips_data = StreamHighlight.objects.filter(user_id=request.user.username, stream_link=stream_obj.stream_link).order_by('id')
    for clip in clips_data:
         if clip.clip_link and not clip.clip_link.startswith(('http://', 'https://', '/')):
             try: # Versuche URL mit settings zu bauen
                 clip.media_url = settings.MEDIA_URL + clip.clip_link.replace('\\','/')
             except Exception: clip.media_url = None # Fallback
         else: clip.media_url = clip.clip_link
    response_data = { "name": request.user.username, "stream": stream_obj, "is_staff": request.user.is_staff, "clips_data": clips_data }
    return render(request, 'viewer/stream.html', response_data)

# --- STREAM LÖSCHEN ---
@login_required
@require_POST
def delete_stream(request, stream_id):
    # ... (Bleibt wie die verbesserte Version aus der vorigen Antwort) ...
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    stream_title_for_log = stream_obj.stream_name or stream_obj.stream_link
    print(f"Attempting to delete Stream ID: {stream_id} ('{stream_title_for_log}') for User: {request.user.username}")

    # 1. Pfade sammeln
    video_full_path = None
    csv_full_path = None
    highlight_files_to_delete = []
    stream_dir = None

    if stream_obj.video_file and hasattr(stream_obj.video_file, 'path'):
        video_full_path = stream_obj.video_file.path
        stream_dir = os.path.dirname(video_full_path)

    if stream_obj.sound_csv_path:
        if os.path.isabs(stream_obj.sound_csv_path):
             csv_full_path = stream_obj.sound_csv_path
        elif settings.MEDIA_ROOT: # Nur wenn MEDIA_ROOT verfügbar ist
             csv_full_path = os.path.join(settings.MEDIA_ROOT, stream_obj.sound_csv_path)
        if csv_full_path and not os.path.exists(csv_full_path):
             print(f"Warning: Sound CSV path stored but file not found at {csv_full_path}")
             csv_full_path = None

    highlights = StreamHighlight.objects.filter(user_id=request.user.username, stream_link=stream_obj.stream_link)
    for hl in highlights:
        try:
            if hl.clip_link and settings.MEDIA_ROOT:
                 clip_full_path = os.path.join(settings.MEDIA_ROOT, hl.clip_link)
                 highlight_files_to_delete.append(clip_full_path)
        except Exception as e_path: print(f"Warning: Could not construct path for highlight clip {hl.clip_link}: {e_path}")

    # 2. Datenbank-Einträge löschen
    print(f"Deleting {highlights.count()} highlight DB entries for stream '{stream_title_for_log}'...")
    highlights.delete()
    print(f"Deleting Stream DB entry (ID: {stream_id})...")
    stream_obj.delete()

    # 3. Dateien löschen
    print(f"Deleting {len(highlight_files_to_delete)} associated highlight files...")
    for file_path in highlight_files_to_delete:
        if file_path and os.path.exists(file_path):
            try: os.remove(file_path); print(f"  Deleted: {file_path}")
            except OSError as e_del_clip: print(f"  Error deleting {file_path}: {e_del_clip}")
        else: print(f"  File not found or path invalid: {file_path}")

    if csv_full_path and os.path.exists(csv_full_path):
        try: os.remove(csv_full_path); print(f"Deleted analysis CSV: {csv_full_path}")
        except OSError as e_del_csv: print(f"Error deleting analysis CSV {csv_full_path}: {e_del_csv}")

    if video_full_path and os.path.exists(video_full_path):
        try: os.remove(video_full_path); print(f"Deleted video file: {video_full_path}")
        except OSError as e_del_vid: print(f"Error deleting video file {video_full_path}: {e_del_vid}")

    if stream_dir and os.path.exists(stream_dir):
        try:
            if not os.listdir(stream_dir): os.rmdir(stream_dir); print(f"Deleted empty stream directory: {stream_dir}")
            else: print(f"Stream directory not empty, not deleting: {stream_dir}")
        except OSError as e_rmdir: print(f"Error deleting stream directory {stream_dir}: {e_rmdir}")

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

# --- GENERATOR SEITE (ANGEPASST) ---
@login_required
def generator_view(request, stream_id):
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    clips_data = StreamHighlight.objects.filter(user_id=request.user.username, stream_link=stream_obj.stream_link).order_by('id')

    # Media-URL für Clips hinzufügen
    for clip in clips_data:
         if clip.clip_link and not clip.clip_link.startswith(('http://', 'https://', '/')):
             try: clip.media_url = settings.MEDIA_URL + clip.clip_link.replace('\\','/')
             except Exception: clip.media_url = None
         else: clip.media_url = clip.clip_link

    context = {
        'stream': stream_obj, # Enthält jetzt avg_loudness, p90_loudness etc.
        'name': request.user.username,
        'is_staff': request.user.is_staff,
        'clips_data': clips_data,
        # Verwende den Threshold aus den Models/Analyse oder einen Default
        'current_threshold': LOUDNESS_THRESHOLD
    }
    return render(request, 'viewer/generator.html', context)


# --- NEUE VIEW: HIGHLIGHTS NEU GENERIEREN ---
@login_required
# @require_POST # Besser POST verwenden, wenn Threshold übergeben wird
def regenerate_highlights_view(request, stream_id):
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    print(f"--- Received request to regenerate highlights for Stream ID: {stream_id} ---")

    # Baue CSV-Pfad sicher zusammen
    sound_csv_path = None
    if stream_obj.sound_csv_path:
        if os.path.isabs(stream_obj.sound_csv_path):
             sound_csv_path = stream_obj.sound_csv_path
        elif settings.MEDIA_ROOT:
             sound_csv_path = os.path.join(settings.MEDIA_ROOT, stream_obj.sound_csv_path)

    video_path = None
    if stream_obj.video_file and hasattr(stream_obj.video_file, 'path'):
         video_path = stream_obj.video_file.path

    if not video_path or not os.path.exists(video_path):
         print(f"ERROR: Original video file not found for Stream ID: {stream_id}")
         messages.error(request, "Original Video nicht gefunden.")
         return redirect('generator', stream_id=stream_id)

    if not sound_csv_path or not os.path.exists(sound_csv_path):
         print(f"ERROR: Sound analysis CSV not found for Stream ID: {stream_id} at {sound_csv_path}")
         messages.error(request, "Analyse-Daten (CSV) nicht gefunden. Bitte Analyse neu starten (Video erneut hochladen).")
         return redirect('generator', stream_id=stream_id)

    # Führe Highlight-Findung erneut aus (Synchron - kann dauern!)
    # HIER wäre Threading/Celery besser
    current_threshold = LOUDNESS_THRESHOLD # Hole den Default aus analysis.py
    print(f"Regenerating clips using threshold: {current_threshold}")
    try:
        find_highlights_by_loudness(
            sound_csv_path=sound_csv_path,
            video_path=video_path,
            stream_id=stream_id,
            user_name=request.user.username,
            threshold=current_threshold # Übergib den Threshold
        )
        print(f"--- Finished regenerating highlights for Stream ID: {stream_id} ---")
        messages.success(request, "Highlights wurden neu generiert.")
    except Exception as e_regen:
         print(f"ERROR during highlight regeneration for Stream ID {stream_id}: {e_regen}")
         traceback.print_exc()
         messages.error(request, "Fehler bei der Neugenerierung der Highlights.")

    return redirect('generator', stream_id=stream_id)