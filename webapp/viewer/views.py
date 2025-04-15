# D:\Neuer_Ordner\AI_Highlight_Clipper\webapp\viewer\views.py

from django.shortcuts import render, redirect, get_object_or_404 # get_object_or_404 hinzugefügt
from django.http import HttpResponse, JsonResponse
from django.contrib.auth.forms import UserCreationForm # Für Registrierung
from django.contrib.auth import login, authenticate   # Für Login
from django.contrib.auth.decorators import login_required # Um Views zu schützen
import os

# Lokale Imports
from .models import Stream, StreamHighlight
from .forms import StreamUploadForm # Für den Datei-Upload


# --- INDEX / LOGIN / HAUPTSEITE ---
# (Bleibt wie vorher)
def index(request):
    # ... (Code wie vorher) ...
    if request.method == 'POST':
        # --- Login-Logik ---
        username_post = request.POST.get('username')
        password_post = request.POST.get('password')
        user_auth = authenticate(request, username=username_post, password=password_post)

        if user_auth is not None:
            login(request, user_auth)
            return redirect('index') # Zur Hauptseite nach Login
        else:
            form_signup = UserCreationForm()
            return render(request, 'viewer/index.html', {'form': form_signup, 'login_error': True})

    if request.user.is_authenticated:
        stream_data = Stream.objects.filter(user_id=request.user.username).order_by('-id')
        upload_form = StreamUploadForm()
        response_data = {
            "stream_data": stream_data,
            "name": request.user.username,
            "is_staff": request.user.is_staff,
            "upload_form": upload_form
        }
        return render(request, 'viewer/main.html', response_data)
    else:
        form_signup = UserCreationForm()
        return render(request, 'viewer/index.html', {'form': form_signup})


# --- STREAM/VIDEO HOCHLADEN ---
# (Bleibt wie vorher)
@login_required # Nur eingeloggte User können hochladen
def add_stream(request):
    # ... (Code wie vorher) ...
    if request.method == 'POST':
        form = StreamUploadForm(request.POST, request.FILES)
        if form.is_valid():
            new_stream = form.save(commit=False)
            new_stream.user_id = request.user.username
            if new_stream.video_file and not new_stream.stream_name:
                 new_stream.stream_name = os.path.splitext(new_stream.video_file.name)[0]
            new_stream.save()
            print(f"Video '{new_stream.video_file.name}' saved for user {new_stream.user_id} at: {new_stream.video_file.path}")
            return redirect('index')
        else:
             stream_data = Stream.objects.filter(user_id=request.user.username).order_by('-id')
             response_data = {
                 "stream_data": stream_data,
                 "name": request.user.username,
                 "is_staff": request.user.is_staff,
                 "upload_form": form
             }
             return render(request, 'viewer/main.html', response_data)
    else:
        return redirect('index')


# --- CLIP HINZUFÜGEN (für spätere Analyse) ---
# (Bleibt wie vorher)
def add_clip(request):
    # ... (Code wie vorher) ...
    user_name = request.GET.get('user_name')
    stream_link = request.GET.get('stream_link')
    clip_link = request.GET.get('clip_link')

    if user_name and stream_link and clip_link:
        print(f"Received clip data: User={user_name}, Stream={stream_link}, Clip={clip_link}")
        StreamHighlight.objects.create(clip_link=clip_link, stream_link=stream_link, user_id=user_name)
        return JsonResponse({'status': 'clip added'})
    else:
        return JsonResponse({'status': 'error', 'message': 'Missing parameters'}, status=400)


# --- ALTE STREAM DETAILSEITE ANZEIGEN (zeigt Highlights) ---
# (Bleibt wie vorher, wird aber nicht mehr direkt von main.html verlinkt)
@login_required
def stream(request, stream_id):
    # ... (Code wie vorher) ...
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    clips_data = StreamHighlight.objects.filter(user_id=request.user.username, stream_link=stream_obj.stream_link)
    response_data = {
        "name": request.user.username,
        "stream": stream_obj,
        "is_staff": request.user.is_staff,
        "clips_data": clips_data
    }
    return render(request, 'viewer/stream.html', response_data)


# --- STREAM LÖSCHEN ---
# (Bleibt wie vorher)
@login_required
def delete_stream(request, user_name, stream_link):
    # ... (Code wie vorher) ...
    if request.user.username != user_name:
        return HttpResponse("Unauthorized", status=403)

    deleted_count, _ = Stream.objects.filter(stream_link=stream_link, user_id=user_name).delete()
    StreamHighlight.objects.filter(stream_link=stream_link, user_id=user_name).delete()

    if deleted_count > 0:
        print(f"Deleted stream '{stream_link}' for user '{user_name}'.")
    else:
        print(f"Stream '{stream_link}' not found for user '{user_name}' during deletion attempt.")
    return redirect('index')


# --- NEUE VIEW für den Videoplayer ---
@login_required
def video_player_view(request, stream_id):
    '''
    Zeigt das Originalvideo und einen Link zur Generator-Seite.
    '''
    # Hole das Stream-Objekt sicher oder zeige 404
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)

    context = {
        'stream': stream_obj,
        'name': request.user.username, # Für Header etc.
        'is_staff': request.user.is_staff, # Für Header etc.
    }
    # Rendere das NEUE Template
    return render(request, 'viewer/video_player.html', context)


# --- NEUE VIEW für die Generator-Seite (Platzhalter) ---
@login_required
def generator_view(request, stream_id):
    '''
    Platzhalter-Seite, von der aus die Analyse gestartet werden könnte.
    '''
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)

    context = {
        'stream': stream_obj,
        'name': request.user.username,
        'is_staff': request.user.is_staff,
    }
    # Rendere das NEUE Template
    return render(request, 'viewer/generator.html', context)