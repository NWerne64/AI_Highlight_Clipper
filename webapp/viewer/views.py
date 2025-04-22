# AI_Highlight_Clipper/webapp/viewer/views.py

from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse, HttpResponseRedirect
from django.urls import reverse # Für Redirect nach Re-Generierung
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login, authenticate
from django.contrib.auth.decorators import login_required
from django.conf import settings
import os
import threading
import traceback

from .models import Stream, StreamHighlight
from .forms import StreamUploadForm
# Importiere BEIDE Analyse-Funktionen
from .analysis import run_analysis_and_extraction_thread, find_highlights_by_loudness, LOUDNESS_THRESHOLD # Importiere auch den default Threshold

# --- INDEX, ADD_STREAM, ADD_CLIP, STREAM(alt), DELETE_STREAM, VIDEO_PLAYER_VIEW ---
# (Views bis auf generator_view und die neue regenerate... bleiben unverändert)
# --- INDEX / LOGIN / HAUPTSEITE ---
def index(request):
    # ... (Code wie zuletzt) ...
    if request.method == 'POST':
        username_post = request.POST.get('username')
        password_post = request.POST.get('password')
        user_auth = authenticate(request, username=username_post, password=password_post)
        if user_auth is not None:
            login(request, user_auth)
            return redirect('index')
        else:
            form_signup = UserCreationForm()
            return render(request, 'viewer/index.html', {'form': form_signup, 'login_error': True})

    if request.user.is_authenticated:
        stream_data = Stream.objects.filter(user_id=request.user.username).order_by('-id')
        upload_form = StreamUploadForm()
        response_data = { "stream_data": stream_data, "name": request.user.username, "is_staff": request.user.is_staff, "upload_form": upload_form }
        return render(request, 'viewer/main.html', response_data)
    else:
        form_signup = UserCreationForm()
        return render(request, 'viewer/index.html', {'form': form_signup})

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
            # Setze Status auf PENDING bevor gespeichert wird
            new_stream.analysis_status = 'PENDING'
            new_stream.save()
            print(f"Video '{new_stream.video_file.name}' saved for user {new_stream.user_id} at path: {new_stream.video_file.path}")
            print(f"Stream object created with ID: {new_stream.id}, Status: PENDING")

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
                    # Setze Status auf PROCESSING direkt nach Thread-Start
                    new_stream.analysis_status = 'PROCESSING'
                    new_stream.save(update_fields=['analysis_status'])
                else:
                     print(f"ERROR: Video file path not found or file does not exist for Stream ID {new_stream.id}. Analysis thread not started.")
                     new_stream.analysis_status = 'ERROR' # Setze Fehlerstatus
                     new_stream.save(update_fields=['analysis_status'])

            except Exception as e_thread:
                print(f"ERROR starting analysis thread for Stream ID {new_stream.id}: {e_thread}")
                traceback.print_exc()
                new_stream.analysis_status = 'ERROR' # Setze Fehlerstatus
                new_stream.save(update_fields=['analysis_status'])

            return redirect('index')
        else:
             stream_data = Stream.objects.filter(user_id=request.user.username).order_by('-id')
             response_data = { "stream_data": stream_data, "name": request.user.username, "is_staff": request.user.is_staff, "upload_form": form }
             return render(request, 'viewer/main.html', response_data)
    else:
        return redirect('index')

# --- CLIP HINZUFÜGEN ---
def add_clip(request):
    # ... (Code bleibt wie zuletzt) ...
    print("WARNUNG: /add_clip/ Endpunkt aufgerufen (wahrscheinlich veraltet).")
    return JsonResponse({'status': 'add_clip endpoint reached (likely unused)'})

# --- STREAM DETAILSEITE (Highlights) ---
@login_required
def stream(request, stream_id):
    # ... (Code bleibt wie zuletzt) ...
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    clips_data = StreamHighlight.objects.filter(user_id=request.user.username, stream_link=stream_obj.stream_link).order_by('id')
    for clip in clips_data:
         if clip.clip_link and not clip.clip_link.startswith(('http://', 'https://', '/')):
             clip.media_url = settings.MEDIA_URL + clip.clip_link.replace('\\','/')
         else: clip.media_url = clip.clip_link
    response_data = { "name": request.user.username, "stream": stream_obj, "is_staff": request.user.is_staff, "clips_data": clips_data }
    return render(request, 'viewer/stream.html', response_data)

# --- STREAM LÖSCHEN ---
@login_required
def delete_stream(request, user_name, stream_link):
    # ... (Code bleibt wie zuletzt, mit Dateilöschung) ...
    if request.user.username != user_name: return HttpResponse("Unauthorized", status=403)
    streams_to_delete = Stream.objects.filter(stream_link=stream_link, user_id=user_name)
    deleted_stream_count = 0
    for stream_instance in streams_to_delete:
        # Zugehörige Highlight-Dateien löschen
        highlights_to_delete = StreamHighlight.objects.filter(user_id=user_name, stream_link=stream_instance.stream_link)
        for hl in highlights_to_delete:
            try:
                clip_full_path = os.path.join(settings.MEDIA_ROOT, hl.clip_link)
                if os.path.exists(clip_full_path): os.remove(clip_full_path); print(f"Deleted clip file: {clip_full_path}")
                else: print(f"Clip file not found: {clip_full_path}")
            except Exception as e_del_clip: print(f"Warning: Could not delete clip file {hl.clip_link}: {e_del_clip}")
        # Zugehörige Videodatei löschen
        if stream_instance.video_file and hasattr(stream_instance.video_file, 'path') and os.path.exists(stream_instance.video_file.path):
             try:
                 video_path_to_delete = stream_instance.video_file.path
                 print(f"Deleting video file: {video_path_to_delete}")
                 os.remove(video_path_to_delete)
                 video_dir = os.path.dirname(video_path_to_delete)
                 if not os.listdir(video_dir): os.rmdir(video_dir); print(f"Deleting empty stream dir: {video_dir}")
             except OSError as e_del_vid: print(f"Warning: Could not delete video file/dir {video_path_to_delete}: {e_del_vid}")
        # Analyse-CSV löschen, falls Pfad gespeichert wurde
        if stream_instance.sound_csv_path and os.path.exists(stream_instance.sound_csv_path):
             try: os.remove(stream_instance.sound_csv_path); print(f"Deleting analysis CSV: {stream_instance.sound_csv_path}")
             except OSError as e_del_csv: print(f"Warning: Could not delete analysis CSV {stream_instance.sound_csv_path}: {e_del_csv}")
        # Lösche Stream-Objekt aus DB
        d_count, _ = stream_instance.delete()
        deleted_stream_count += d_count
    if deleted_stream_count > 0: print(f"Deleted stream '{stream_link}' for user '{user_name}'.")
    else: print(f"Stream '{stream_link}' not found.")
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
    '''
    Zeigt die Generator-Seite, lädt Highlights und Analyse-Statistiken.
    '''
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    clips_data = StreamHighlight.objects.filter(
        user_id=request.user.username,
        stream_link=stream_obj.stream_link
    ).order_by('id')

    # Füge Media-URL zu Clips hinzu
    for clip in clips_data:
         if clip.clip_link and not clip.clip_link.startswith(('http://', 'https://', '/')):
             clip.media_url = settings.MEDIA_URL + clip.clip_link.replace('\\','/')
         else: clip.media_url = clip.clip_link

    context = {
        'stream': stream_obj, # Enthält jetzt avg_loudness, p90_loudness etc.
        'name': request.user.username,
        'is_staff': request.user.is_staff,
        'clips_data': clips_data,
        'default_threshold': LOUDNESS_THRESHOLD # Übergebe den Standard-Threshold
    }
    return render(request, 'viewer/generator.html', context)


# --- NEUE VIEW: HIGHLIGHTS NEU GENERIEREN ---
@login_required
def regenerate_highlights_view(request, stream_id):
    '''
    Liest die vorhandene Lautstärke-CSV und generiert Highlights neu
    mit dem aktuell in analysis.py definierten LOUDNESS_THRESHOLD.
    (Iteration 3 würde hier den Threshold aus dem Request holen)
    '''
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    print(f"--- Received request to regenerate highlights for Stream ID: {stream_id} ---")

    # Finde den Pfad zur CSV und zum Originalvideo
    sound_csv_path = stream_obj.sound_csv_path
    video_path = None
    if stream_obj.video_file and hasattr(stream_obj.video_file, 'path'):
         video_path = stream_obj.video_file.path

    if not video_path or not os.path.exists(video_path):
         print(f"ERROR: Original video file not found for Stream ID: {stream_id}")
         # Optional: Nachricht an User senden
         return redirect('generator', stream_id=stream_id) # Zurück zur Generator-Seite

    if not sound_csv_path or not os.path.exists(sound_csv_path):
         print(f"ERROR: Sound analysis CSV not found for Stream ID: {stream_id} at {sound_csv_path}")
         # Optional: Nachricht an User senden / Analyse neu starten?
         return redirect('generator', stream_id=stream_id) # Zurück

    # Führe die Highlight-Findung erneut aus (im Hauptthread - kann dauern!)
    # BESSER: Auch dies in einen Thread auslagern! Aber erstmal synchron:
    print(f"Regenerating clips using threshold: {LOUDNESS_THRESHOLD}")
    try:
        # Rufe die Funktion auf, die Highlights findet und Clips erstellt/speichert
        find_highlights_by_loudness(
            sound_csv_path=sound_csv_path,
            video_path=video_path,
            stream_id=stream_id,
            user_name=request.user.username,
            threshold=LOUDNESS_THRESHOLD # Verwende den aktuellen Standard-Threshold
        )
        print(f"--- Finished regenerating highlights for Stream ID: {stream_id} ---")
    except Exception as e_regen:
         print(f"ERROR during highlight regeneration for Stream ID {stream_id}: {e_regen}")
         traceback.print_exc()
         # Optional: Nachricht an User senden

    # Leite zurück zur Generator-Seite, um die neuen Clips anzuzeigen
    return redirect('generator', stream_id=stream_id)