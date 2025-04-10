# D:\Neuer_Ordner\AI_Highlight_Clipper\webapp\viewer\views.py

from django.shortcuts import render, redirect
from django.http import HttpResponse, JsonResponse
from django.contrib.auth.forms import UserCreationForm # Für Registrierung
from django.contrib.auth import login, authenticate   # Für Login
import os

# Lokale Imports
from .models import Stream, StreamHighlight
from .forms import StreamUploadForm # Für den Datei-Upload


# --- INDEX / LOGIN / HAUPTSEITE ---
def index(request):
    '''
    Renders login page (GET) or handles login (POST).
    If logged in (GET), renders main page with stream list and upload form.
    '''
    if request.method == 'POST':
        # --- Login-Logik ---
        username_post = request.POST.get('username')
        password_post = request.POST.get('password')
        user_auth = authenticate(request, username=username_post, password=password_post)

        if user_auth is not None:
            login(request, user_auth)
            return redirect('index') # Zur Hauptseite nach Login
        else:
            # Fehlgeschlagener Login
            form_signup = UserCreationForm()
            # Zeige Login-Seite erneut mit Fehlermeldung
            return render(request, 'viewer/index.html', {'form': form_signup, 'login_error': True})

    # --- GET Request ---
    if request.user.is_authenticated:
        # Eingeloggt: Zeige Hauptseite (main.html)
        stream_data = Stream.objects.filter(user_id=request.user.username).order_by('-id') # Neueste zuerst
        upload_form = StreamUploadForm()
        response_data = {
            "stream_data": stream_data,
            "name": request.user.username,
            "is_staff": request.user.is_staff,
            "upload_form": upload_form
        }
        return render(request, 'viewer/main.html', response_data)
    else:
        # Nicht eingeloggt: Zeige Login/Registrierungsseite (index.html)
        form_signup = UserCreationForm()
        return render(request, 'viewer/index.html', {'form': form_signup})

# --- STREAM/VIDEO HOCHLADEN ---
def add_stream(request):
    '''
    Handles the video file upload form submission (POST).
    Redirects GET requests to the main page.
    '''
    if not request.user.is_authenticated:
         return redirect('index')

    if request.method == 'POST':
        form = StreamUploadForm(request.POST, request.FILES)
        if form.is_valid():
            new_stream = form.save(commit=False)
            new_stream.user_id = request.user.username
            # Optional: Stream-Namen aus Dateinamen ableiten
            if new_stream.video_file and not new_stream.stream_name:
                 new_stream.stream_name = os.path.splitext(new_stream.video_file.name)[0]

            new_stream.save()
            print(f"Video '{new_stream.video_file.name}' saved for user {new_stream.user_id} at: {new_stream.video_file.path}")
            return redirect('index') # Zurück zur Hauptseite nach Upload
        else:
             # Formular ungültig: Zeige Hauptseite erneut mit dem fehlerhaften Formular
             stream_data = Stream.objects.filter(user_id=request.user.username).order_by('-id')
             response_data = {
                 "stream_data": stream_data,
                 "name": request.user.username,
                 "is_staff": request.user.is_staff,
                 "upload_form": form # Fehlerhaftes Formular übergeben
             }
             return render(request, 'viewer/main.html', response_data)
    else:
        # Direkte GET-Anfragen an /add_stream/ sind nicht vorgesehen
        return redirect('index')


# --- CLIP HINZUFÜGEN (für spätere Analyse) ---
def add_clip(request):
    '''
    Processes incoming request (e.g., from ML server)
    Adds incoming highlight link to the database.
    Currently not actively used if analysis is skipped.
    '''
    user_name = request.GET.get('user_name')
    stream_link = request.GET.get('stream_link')
    clip_link = request.GET.get('clip_link')

    if user_name and stream_link and clip_link:
        print(f"Received clip data: User={user_name}, Stream={stream_link}, Clip={clip_link}")
        StreamHighlight.objects.create(clip_link=clip_link, stream_link=stream_link, user_id=user_name)
        return JsonResponse({'status': 'clip added'})
    else:
        # Parameter fehlen
        # Dein letzter Stand hatte 'noch nichts', ich ändere es zurück zu 'error'
        return JsonResponse({'status': 'error', 'message': 'Missing parameters'}, status=400)


# --- STREAM DETAILSEITE ANZEIGEN ---
def stream(request, stream_id):
    '''
    Renders the detail page for a specific stream ID.
    '''
    if not request.user.is_authenticated:
         return redirect('index')

    try:
        # Hole den Stream nur, wenn er dem eingeloggten User gehört
        stream_obj = Stream.objects.get(id=stream_id, user_id=request.user.username)
    except Stream.DoesNotExist:
        return HttpResponse("Stream not found or access denied.", status=404)

    # Hole zugehörige Highlights
    clips_data = StreamHighlight.objects.filter(user_id=request.user.username, stream_link=stream_obj.stream_link)

    response_data = {
        "name": request.user.username,
        "stream": stream_obj, # Das ganze Stream-Objekt
        "is_staff": request.user.is_staff,
        "clips_data": clips_data
    }
    # Zeige das Stream-Template
    return render(request, 'viewer/stream.html', response_data)


# --- STREAM LÖSCHEN ---
def delete_stream(request, user_name, stream_link):
    '''
    Deletes a stream and its associated highlights for the logged-in user.
    '''
    # Sicherheitscheck: User muss eingeloggt sein und darf nur eigene Streams löschen
    if not request.user.is_authenticated or request.user.username != user_name:
        return HttpResponse("Unauthorized", status=403)

    # Lösche den Stream (Highlights sollten via DB-Constraint mitgelöscht werden, wenn ForeignKey richtig ist)
    deleted_count, _ = Stream.objects.filter(stream_link=stream_link, user_id=user_name).delete()

    # Sicherheitshalber Highlights auch explizit löschen
    StreamHighlight.objects.filter(stream_link=stream_link, user_id=user_name).delete()

    if deleted_count > 0:
        print(f"Deleted stream '{stream_link}' for user '{user_name}'.")
    else:
        print(f"Stream '{stream_link}' not found for user '{user_name}' during deletion attempt.")

    return redirect('index') # Zurück zur Hauptseite