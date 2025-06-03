# AI_Highlight_Clipper/webapp/viewer/views.py

from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse, HttpResponseRedirect
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login, authenticate
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST  # require_POST für generate_highlights_view
from django.conf import settings
from django.core.exceptions import FieldError
from django.core.files.base import ContentFile

from datetime import datetime, timedelta  # timedelta für ChatAnalyzerFromFile wichtig
import cv2  # Für Videolänge in generate_highlights_view
from transformers import pipeline as hf_pipeline  # Für das Sentiment-Modell

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
# import time # Ist schon oben importiert

from .models import Stream, StreamHighlight
from .forms import StreamUploadForm
from .analysis import SoundDetector
from . import twitch_api_client
# NEU: Import für die Chat-Analyse aus Datei
from .chat_analysis import ChatAnalyzerFromFile

# --- Globale Variable und Hilfsfunktion für das Sentiment-Analyse-Modell ---
sentiment_classifier_pipeline = None


def get_sentiment_classifier():
    global sentiment_classifier_pipeline
    if sentiment_classifier_pipeline is None:
        print("INFO: Lade Sentiment-Analyse-Modell (transformers)...")
        try:
            # Du kannst hier auch ein spezifisches Modell angeben, falls gewünscht:
            # sentiment_classifier_pipeline = hf_pipeline('sentiment-analysis', model="nlptown/bert-base-multilingual-uncased-sentiment")
            sentiment_classifier_pipeline = hf_pipeline('sentiment-analysis')
            print("INFO: Sentiment-Analyse-Modell erfolgreich geladen.")
        except Exception as e:
            print(f"FEHLER beim Laden des Sentiment-Analyse-Modells: {e}")
            # Hier könnte man einen Fallback oder eine Fehlermeldung implementieren
    return sentiment_classifier_pipeline


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
        # VIDEO-URL zum Stream-Objekt hinzufügen
        for stream in stream_data:
            if stream.video_file and stream.video_file.name:
                absolute_path = os.path.join(settings.MEDIA_ROOT, stream.video_file.name)
                if os.path.exists(absolute_path):
                    stream.video_url = os.path.join(settings.MEDIA_URL, stream.video_file.name).replace('\\', '/')
                else:
                    stream.video_url = None
            else:
                stream.video_url = None
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


# --- STREAM/VIDEO HOCHLADEN ---
@login_required
def add_stream(request):
    if request.method == 'POST':
        form = StreamUploadForm(request.POST, request.FILES)
        if form.is_valid():
            if not request.FILES.get('video_file'):
                messages.error(request, "Bitte Videodatei auswählen.")
                # Zeige das Formular erneut mit der Fehlermeldung
                stream_data = Stream.objects.filter(user_id=request.user.username).order_by('-id')
                return render(request, 'viewer/main.html', {
                    'upload_form': form,
                    'stream_data': stream_data,
                    'name': request.user.username,
                    'is_staff': request.user.is_staff
                })

            new_stream = form.save(commit=False)
            new_stream.user_id = request.user.username

            if not new_stream.stream_name:  # Wenn der Benutzer keinen Titel angibt
                new_stream.stream_name = os.path.splitext(new_stream.video_file.name)[0]

            new_stream.analysis_status = 'PENDING'  # Status für neu hochgeladene Videos
            new_stream.save()  # Speichere das Objekt, um eine ID zu bekommen, bevor der Pfad finalisiert wird.

            # Zielverzeichnis und Dateiname basierend auf der Stream-ID erstellen
            # Der get_upload_path in models.py wird dies beim Speichern der Datei selbst tun,
            # aber wir brauchen den Pfad hier, um die Datei manuell zu verschieben/umzubenennen.
            user_id_str = str(new_stream.user_id)
            stream_id_str = str(new_stream.id)

            # Temporärer Pfad, wo Django die Datei initial speichert (abhängig von File Storage Settings)
            temp_video_path = new_stream.video_file.path

            # Finaler relativer Pfad in MEDIA_ROOT
            final_relative_dir = os.path.join('uploads', user_id_str, stream_id_str)
            final_filename = f"{stream_id_str}.mp4"  # Standardisiere auf .mp4
            final_relative_path = os.path.join(final_relative_dir, final_filename).replace('\\', '/')

            # Absoluter Zielpfad
            absolute_target_dir = os.path.join(settings.MEDIA_ROOT, final_relative_dir)
            absolute_target_path = os.path.join(absolute_target_dir, final_filename)

            os.makedirs(absolute_target_dir, exist_ok=True)

            try:
                shutil.move(temp_video_path, absolute_target_path)
                new_stream.video_file.name = final_relative_path  # Aktualisiere den Dateipfad im Modell
                new_stream.save(update_fields=['video_file'])  # Speichere die Änderung
                print(f"📦 Video erfolgreich nach '{absolute_target_path}' verschoben und Stream-Objekt aktualisiert.")
                messages.success(request,
                                 f"Video '{new_stream.stream_name}' erfolgreich hochgeladen. Highlights können jetzt generiert werden.")
            except Exception as e:
                print(
                    f"❌ Fehler beim Verschieben der Videodatei von '{temp_video_path}' nach '{absolute_target_path}': {e}")
                new_stream.analysis_status = 'ERROR_NO_FILE'  # Oder ein anderer Fehlerstatus
                new_stream.save(update_fields=['analysis_status'])
                messages.error(request, "Fehler bei der Verarbeitung der hochgeladenen Videodatei.")
                # new_stream.delete() # Optional: Stream-Objekt löschen, wenn Datei nicht verarbeitet werden kann
                return redirect('index')

            return redirect('index')
        else:
            # Formular ist nicht valide, zeige es erneut mit Fehlern
            stream_data = Stream.objects.filter(user_id=request.user.username).order_by('-id')
            # Füge alle notwendigen Kontextvariablen hinzu, die main.html erwartet
            twitch_vods = request.session.get('twitch_vods_context', None)
            searched_channel_name = request.session.get('searched_channel_name_context', None)
            search_attempted = request.session.get('search_attempted_context', False)

            return render(request, 'viewer/main.html', {
                'upload_form': form,
                'stream_data': stream_data,
                'name': request.user.username,
                'is_staff': request.user.is_staff,
                'twitch_vods': twitch_vods,
                'searched_channel_name': searched_channel_name,
                'search_attempted': search_attempted,
                # Füge hier weitere Kontextvariablen hinzu, falls main.html sie benötigt
            })
    else:  # GET Request
        return redirect('index')


# --- NEUE generate_highlights_view ---
@login_required
@require_POST
def generate_highlights_view(request, stream_id):
    stream = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)

    log_prefix = f"[Generate Highlights View Stream {stream_id}] "
    print(f"{log_prefix}Starte Highlight-Generierung für Stream: '{stream.stream_name or stream.id}'")

    if not stream.video_file or not hasattr(stream.video_file, 'path'):  # Prüfe auch auf 'path'
        messages.error(request, "Keine Videodatei für diesen Stream gefunden oder Pfad nicht verfügbar.")
        print(f"{log_prefix}❌ Keine Videodatei oder Pfad für Stream {stream_id} gefunden.")
        return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('stream', args=[stream_id])))

    try:
        video_path = stream.video_file.path
        if not os.path.exists(video_path):
            messages.error(request, f"Videodatei nicht im Dateisystem gefunden: {video_path}")
            print(f"{log_prefix}❌ Videodatei nicht im Dateisystem gefunden: {video_path}")
            stream.analysis_status = 'ERROR_NO_FILE'
            stream.save(update_fields=['analysis_status'])
            return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('stream', args=[stream_id])))

        stream.analysis_status = 'PROCESSING'
        stream.save(update_fields=['analysis_status'])

        stream_dir = os.path.dirname(video_path)

        # --- 1. Sound-Analyse ---
        sound_csv_path = os.path.join(stream_dir, f"{stream_id}_sound.csv")
        labels_list = ['Laughter', 'Cheering', 'Gunshot', 'Music', 'Speech', 'Dog', 'Crowd',
                       'Explosion', 'Applause', 'Scream', 'Laugh', 'Shout', 'Car', 'Siren']
        sound_columns_init = ['start_time', 'end_time', 'sound_loudness'] + labels_list
        empty_sound_df = pd.DataFrame({col: pd.Series(dtype='float') for col in sound_columns_init})

        if not os.path.exists(sound_csv_path):
            print(f"{log_prefix}🎧 Starte SoundDetector für: {video_path}")
            detector = SoundDetector(
                channel_name=str(stream.user_id),
                stream_features=empty_sound_df.copy(),  # Gib eine Kopie, um Seiteneffekte zu vermeiden
                stream_dataframe_name=str(stream_id),
                video_path=video_path
            )
            detector.start()
            for _ in range(60):
                if os.path.exists(sound_csv_path) and os.path.getsize(sound_csv_path) > 0:  # Prüfe auch Größe
                    break
                time.sleep(1)

        if not os.path.exists(sound_csv_path) or os.path.getsize(sound_csv_path) == 0:
            messages.error(request, "Sound-Analyse fehlgeschlagen – keine gültige Sound-CSV gefunden.")
            print(f"{log_prefix}❌ Sound-Analyse fehlgeschlagen - CSV nicht gefunden oder leer: {sound_csv_path}")
            stream.analysis_status = 'ERROR'
            stream.save(update_fields=['analysis_status'])
            return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('stream', args=[stream_id])))

        df_sound = pd.read_csv(sound_csv_path)
        df_sound.rename(columns={'start_time': 'time_offset', 'end_time': 'end_time_sound_offset'}, inplace=True)

        # --- Videolänge ermitteln für Chat-Analyse ---
        video_total_duration_seconds = 0
        try:
            cap = cv2.VideoCapture(video_path)
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if fps > 0 and frame_count > 0:
                video_total_duration_seconds = int(frame_count / fps)
            else:
                video_total_duration_seconds = int(
                    df_sound['end_time_sound_offset'].max()) if not df_sound.empty else 3600
            cap.release()
            print(f"{log_prefix}Videolänge für Chat-Analyse: {video_total_duration_seconds}s")
        except Exception as e_vid_meta:
            print(f"{log_prefix}Konnte Videolänge nicht präzise ermitteln: {e_vid_meta}. Nutze Fallback.")
            video_total_duration_seconds = int(df_sound['end_time_sound_offset'].max()) if not df_sound.empty else 3600

        if video_total_duration_seconds <= 0:  # Sicherstellen, dass es positiv ist
            print(
                f"{log_prefix}WARNUNG: Videodauer ist {video_total_duration_seconds}s. Setze auf 10s für Chat-Analyse.")
            video_total_duration_seconds = 10

        # --- 2. Chat-Analyse ---
        chat_features_csv_filename = f"{stream_id}_chat_features.csv"
        chat_features_csv_path = os.path.join(settings.MEDIA_ROOT, 'chat_analysis_results', chat_features_csv_filename)

        actual_chat_log_path = None
        if stream.chat_log_file and stream.chat_log_file.strip():
            potential_path = os.path.join(settings.MEDIA_ROOT, stream.chat_log_file)
            if os.path.exists(potential_path):
                actual_chat_log_path = potential_path
            else:
                print(
                    f"{log_prefix}WARNUNG: Chat-Log-Datei '{stream.chat_log_file}' in DB vermerkt, aber nicht unter '{potential_path}' gefunden.")

        df_chat = pd.DataFrame()
        if actual_chat_log_path:
            print(f"{log_prefix}💬 Chat-Log gefunden: {actual_chat_log_path}")
            if not os.path.exists(chat_features_csv_path) or os.path.getsize(chat_features_csv_path) == 0:
                print(f"{log_prefix}Starte Chat-Analyse, da Feature-CSV fehlt oder leer ist: {actual_chat_log_path}")

                chat_df_init_cols = ['start_time_offset', 'end_time_offset', 'message_counts', 'positive_message_count',
                                     'negative_message_count']
                chat_df_init = pd.DataFrame(columns=chat_df_init_cols).astype({
                    'start_time_offset': 'float64', 'end_time_offset': 'float64',
                    'message_counts': 'int64', 'positive_message_count': 'int64', 'negative_message_count': 'int64'
                })

                classifier = get_sentiment_classifier()
                if classifier:
                    chat_analyzer = ChatAnalyzerFromFile(
                        stream_features_df=chat_df_init.copy(),  # Gib eine Kopie
                        stream_id_str=str(stream_id),
                        classifier_pipeline=classifier
                    )
                    parsed_chat_messages = chat_analyzer.parse_chat_log(actual_chat_log_path)
                    if parsed_chat_messages:
                        chat_analyzer.process_messages_from_log(
                            parsed_chat_messages,
                            video_total_duration_seconds
                        )
                    else:
                        print(f"{log_prefix}Keine Chat-Nachrichten in Log-Datei gefunden für Analyse.")
                else:
                    print(
                        f"{log_prefix}FEHLER: Sentiment-Analyse-Modell konnte nicht geladen werden. Überspringe Chat-Analyse.")

            if os.path.exists(chat_features_csv_path):
                try:
                    df_chat = pd.read_csv(chat_features_csv_path)
                    print(f"{log_prefix}Chat-Features erfolgreich geladen aus: {chat_features_csv_path}")
                    if df_chat.empty: print(f"{log_prefix}WARNUNG: Geladene Chat-Feature-Datei ist leer.")
                except pd.errors.EmptyDataError:
                    print(f"{log_prefix}WARNUNG: Chat-Feature-Datei ist leer: {chat_features_csv_path}")
                except Exception as e_read_chat_csv:
                    print(
                        f"{log_prefix}FEHLER beim Lesen der Chat-Feature-CSV '{chat_features_csv_path}': {e_read_chat_csv}")
            else:
                print(f"{log_prefix}Chat-Feature-Datei nicht gefunden nach Analyseversuch: {chat_features_csv_path}")
        else:
            print(f"{log_prefix}Keine Chat-Log-Datei für Stream {stream_id} gefunden. Überspringe Chat-Analyse.")

        # --- 3. Features zusammenführen ---
        df_merged = df_sound.copy()
        if not df_chat.empty and all(col in df_chat.columns for col in
                                     ['start_time_offset', 'message_counts', 'positive_message_count',
                                      'negative_message_count']):
            df_merged['interval_key'] = (df_merged['time_offset'] // 10).astype(int)
            df_chat['interval_key'] = (df_chat['start_time_offset'] // 10).astype(int)

            chat_cols_to_agg = ['message_counts', 'positive_message_count', 'negative_message_count']
            df_chat_aggregated = df_chat.groupby('interval_key')[chat_cols_to_agg].sum().reset_index()
            df_merged = pd.merge(df_merged, df_chat_aggregated, on='interval_key', how='left')
        else:
            print(
                f"{log_prefix}WARNUNG: df_chat ist leer oder hat nicht die erwarteten Spalten. Überspringe Merge mit Chat-Daten.")

        chat_metric_cols = ['message_counts', 'positive_message_count', 'negative_message_count']
        for col in chat_metric_cols:
            if col in df_merged.columns:
                df_merged[col] = df_merged[col].fillna(0).astype(int)
            else:
                df_merged[col] = 0

        df_final_features = df_merged

        # --- 4. Highlight Score Berechnung ---
        df_final_features['highlight_score'] = (
                df_final_features['sound_loudness'].fillna(0) * 1.5 +
                df_final_features.get('Laughter', pd.Series(0, index=df_final_features.index)).fillna(0) * 3 +
                df_final_features.get('Gunshot', pd.Series(0, index=df_final_features.index)).fillna(0) * 4 +
                df_final_features.get('Explosion', pd.Series(0, index=df_final_features.index)).fillna(0) * 2 +
                df_final_features.get('Scream', pd.Series(0, index=df_final_features.index)).fillna(0) * 2 +
                df_final_features.get('Applause', pd.Series(0, index=df_final_features.index)).fillna(0) * 1.5 +
                df_final_features.get('Cheering', pd.Series(0, index=df_final_features.index)).fillna(0) * 1.5 +
                df_final_features['message_counts'].fillna(0) * 0.05 +
                df_final_features['positive_message_count'].fillna(0) * 0.2 -
                df_final_features['negative_message_count'].fillna(0) * 0.1
        )

        top_clips_num = getattr(settings, 'TOP_N_HIGHLIGHTS', 3)
        top_clips = df_final_features.sort_values(by='highlight_score', ascending=False).head(top_clips_num)

        # --- 5. Gründe berechnen und Clips extrahieren ---
        label_de = {
            'Laughter': 'Lachen', 'Cheering': 'Jubel', 'Gunshot': 'Schuss', 'Music': 'Musik',
            'Speech': 'Sprache', 'Dog': 'Hund', 'Crowd': 'Menschenmenge', 'Explosion': 'Explosion',
            'Applause': 'Applaus', 'Scream': 'Schrei', 'Laugh': 'Lachen', 'Shout': 'Rufen',
            'Car': 'Auto', 'Siren': 'Sirene'
        }

        def extract_reason_extended(row, sound_labels, df_all_features, threshold=0.2):
            reasons = []
            for col in sound_labels:
                if col in row.index and pd.notna(row[col]) and row[col] > threshold:  # Prüfe ob Spalte im row Index ist
                    reasons.append(label_de.get(col, col))

            if 'message_counts' in row.index and row.get('message_counts', 0) > 5:
                if ('positive_message_count' in row.index and
                        (row.get('positive_message_count', 0) / (row.get('message_counts', 0) + 1e-6)) > 0.6):
                    reasons.append("Positiver Chat")
                elif row.get('message_counts', 0) > 10:
                    reasons.append("Hohe Chat-Aktivität")

            if not reasons and 'sound_loudness' in row.index and row.get('sound_loudness', 0) > (
                    df_all_features['sound_loudness'].mean() + df_all_features['sound_loudness'].std()):
                reasons.append("Allgemeine Lautstärke")

            return ", ".join(reasons) if reasons else "Interessantes Segment"

        top_clips['reason'] = top_clips.apply(lambda row: extract_reason_extended(row, labels_list, df_final_features),
                                              axis=1)

        media_relative_dir = os.path.join('uploads', stream.user_id, str(stream_id), 'highlights')
        media_full_dir = os.path.join(settings.MEDIA_ROOT, media_relative_dir)
        os.makedirs(media_full_dir, exist_ok=True)

        StreamHighlight.objects.filter(user_id=stream.user_id, stream_link=stream.stream_link).delete()

        def sec_to_ts(sec):
            return time.strftime('%H:%M:%S', time.gmtime(float(sec))) + '.000'

        reasons_summary = []
        if not top_clips.empty:
            for idx, row_tuple in enumerate(top_clips.itertuples()):
                clip_start_seconds = row_tuple.time_offset
                clip_end_seconds = row_tuple.end_time_sound_offset
                duration_seconds = clip_end_seconds - clip_start_seconds

                if duration_seconds <= 0:
                    print(
                        f"{log_prefix}WARNUNG: Ungültige Dauer ({duration_seconds}s) für Highlight {idx + 1}. Überspringe.")
                    continue

                start_ts_ffmpeg = sec_to_ts(clip_start_seconds)
                duration_str_ffmpeg = str(max(0.1, duration_seconds))  # Mindestdauer für ffmpeg

                clip_filename = f"highlight_{idx + 1}.mp4"
                clip_path_absolute = os.path.join(media_full_dir, clip_filename)
                clip_path_relative_for_db = os.path.join(media_relative_dir, clip_filename).replace('\\', '/')

                ffmpeg_cmd_list = [
                    getattr(settings, 'FFMPEG_PATH', 'ffmpeg'),
                    "-ss", start_ts_ffmpeg,
                    "-i", video_path,
                    "-t", duration_str_ffmpeg,
                    "-c:v", "libx264",  # Ändere auf libx264 für Neukodierung
                    "-preset", "fast",  # Schnellere Kodierung
                    "-crf", "23",  # Akzeptable Qualität
                    "-c:a", "aac",  # Standard Audio Codec
                    "-strict", "-2",  # Für aac
                    clip_path_absolute, "-y"
                ]
                print(f"{log_prefix}Running FFMPEG: {' '.join(ffmpeg_cmd_list)}")
                try:
                    result = subprocess.run(ffmpeg_cmd_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            check=False)  # check=False für manuelles Prüfen
                    if result.returncode != 0:
                        print(f"{log_prefix}❌ FFMPEG Fehler für Highlight {idx + 1}. RC: {result.returncode}")
                        if result.stderr: print(f"{log_prefix}FFMPEG Stderr: {result.stderr.decode(errors='replace')}")
                        # Überspringe dieses Highlight, fahre mit dem nächsten fort
                        messages.warning(request, f"Fehler beim Erstellen von Clip {clip_filename}.")
                        continue

                    if not os.path.exists(clip_path_absolute) or os.path.getsize(
                            clip_path_absolute) < 100:  # Mindestgröße für Clip
                        print(
                            f"{log_prefix}❌ FFMPEG hat keinen gültigen Clip für Highlight {idx + 1} erstellt (Datei fehlt oder ist zu klein).")
                        messages.warning(request, f"Fehler: Clip {clip_filename} wurde nicht korrekt erstellt.")
                        continue

                    StreamHighlight.objects.create(
                        user_id=stream.user_id,
                        stream_link=stream.stream_link,
                        clip_link=clip_path_relative_for_db,
                        reason=row_tuple.reason,
                        start_time=clip_start_seconds
                    )
                    reasons_summary.append(f"{clip_filename}: {row_tuple.reason}")
                    print(f"{log_prefix}📌 Highlight {clip_filename} erstellt → Grund: {row_tuple.reason}")
                except subprocess.CalledProcessError as e_ffmpeg:  # Sollte durch check=False nicht mehr ausgelöst werden
                    print(f"{log_prefix}❌ FFMPEG CalledProcessError für Highlight {idx + 1}: {e_ffmpeg}")
                    if e_ffmpeg.stderr: print(f"{log_prefix}FFMPEG Stderr: {e_ffmpeg.stderr.decode(errors='replace')}")
                except Exception as e_clip_create:
                    print(
                        f"{log_prefix}❌ Fehler beim Erstellen von DB-Eintrag/Speichern von Highlight {idx + 1}: {e_clip_create}")
                    traceback.print_exc()

        if not reasons_summary:  # Wenn keine Clips erfolgreich erstellt wurden
            messages.info(request,
                          "Keine signifikanten Highlights basierend auf den aktuellen Kriterien gefunden oder Clips konnten nicht erstellt werden.")
            print(f"{log_prefix}Keine Top-Clips gefunden oder Fehler bei Erstellung.")
        else:
            messages.success(request, f"{len(reasons_summary)} Highlights wurden erfolgreich generiert.")
            for reason_msg in reasons_summary:
                messages.info(request, reason_msg)

        stream.analysis_status = 'COMPLETE'
        stream.save(update_fields=['analysis_status'])

    except Exception as e:
        print(f"{log_prefix}❌ Unerwarteter Fehler bei Highlight-Generierung: {e}")
        traceback.print_exc()
        messages.error(request, f"Fehler bei der Highlight-Generierung: {str(e)[:200]}")  # Gekürzte Fehlermeldung
        if stream:
            stream.analysis_status = 'ERROR'
            stream.save(update_fields=['analysis_status'])

    return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('stream', args=[stream_id])))


# --- TWITCH STREAM AUFNAHME STARTEN ---
@login_required
def record_stream_view(request):
    if request.method == 'POST':
        twitch_username = request.POST.get('twitch_username', '').strip()
        quality = request.POST.get('quality', '480p')  # Standardqualität
        if not twitch_username:
            messages.error(request, "Bitte Kanalnamen eingeben.")
            return redirect('index')

        client_id = getattr(settings, 'TWITCH_CLIENT_ID', None)
        client_secret = getattr(settings, 'TWITCH_CLIENT_SECRET', None)
        recorder_script_path = getattr(settings, 'TWITCH_RECORDER_SCRIPT_PATH', None)

        # Hole Pfade zu ffmpeg und streamlink aus Django Settings oder verwende Defaults
        ffmpeg_path = getattr(settings, 'FFMPEG_PATH', 'ffmpeg')
        streamlink_path = getattr(settings, 'STREAMLINK_PATH', 'streamlink')

        if not all([client_id, client_secret, recorder_script_path]):
            messages.error(request,
                           "Fehler: Twitch-Konfiguration (Client ID/Secret) oder Recorder-Skript-Pfad fehlt in den Django Settings.")
            return redirect('index')
        if not os.path.exists(recorder_script_path):
            messages.error(request, f"Fehler: Recorder-Skript nicht gefunden unter: {recorder_script_path}")
            return redirect('index')

        # Optional: Flag-Verzeichnis (falls vom Recorder-Skript benötigt)
        # flags_dir_path = os.path.join(settings.BASE_DIR, 'scripts', 'recorder_flags')
        # os.makedirs(flags_dir_path, exist_ok=True)

        try:
            stream_obj = Stream.objects.create(
                user_id=request.user.username,
                stream_link=twitch_username.lower(),  # Kanalname als stream_link
                stream_name=f"Aufnahme: {twitch_username}",
                analysis_status='RECORDING_SCHEDULED'  # Status, dass Aufnahme geplant ist
            )
            stream_id_for_script = stream_obj.id
            print(f"INFO: Stream-Objekt ID {stream_id_for_script} für Aufnahme von '{twitch_username}' erstellt.")
        except Exception as e_db_create:
            print(f"FEHLER beim Erstellen des Stream-Objekts für Aufnahme: {e_db_create}");
            traceback.print_exc()
            messages.error(request, "Datenbankfehler beim Planen der Aufnahme.")
            return redirect('index')

        # Pfade für die Aufnahme erstellen
        video_full_path_for_script = None
        try:
            user_id_part = str(request.user.username)
            stream_id_part = str(stream_id_for_script)
            output_filename = f"{stream_id_part}.mp4"  # Standard Dateiname

            # Relativer Pfad (bezogen auf MEDIA_ROOT) für Speicherung im Modell
            relative_video_dir = os.path.join('uploads', user_id_part, stream_id_part)
            relative_file_path_for_db = os.path.join(relative_video_dir, output_filename).replace('\\', '/')

            # Absoluter Pfad für das Recorder-Skript
            absolute_video_dir = os.path.join(settings.MEDIA_ROOT, relative_video_dir)
            os.makedirs(absolute_video_dir, exist_ok=True)
            video_full_path_for_script = os.path.join(absolute_video_dir, output_filename)

            stream_obj.video_file.name = relative_file_path_for_db  # Speichere relativen Pfad
            stream_obj.save(update_fields=['video_file'])
            print(f"INFO: Zielpfad für Aufnahme (absolut für Skript): {video_full_path_for_script}")
            print(f"INFO: video_file.name in DB gesetzt auf (relativ zu MEDIA_ROOT): {stream_obj.video_file.name}")

        except Exception as e_path:
            print(f"FEHLER beim Erstellen der Aufnahmepfade oder Speichern in DB: {e_path}");
            traceback.print_exc()
            messages.error(request, "Pfadfehler oder DB-Fehler beim Vorbereiten der Aufnahme.")
            if stream_obj: stream_obj.delete()  # Lösche das fehlerhafte Stream-Objekt
            return redirect('index')

        # Kommando zum Starten des Hintergrund-Recorders
        command = [
            sys.executable, recorder_script_path,
            '--username', twitch_username,
            '--quality', quality,
            '--uid', str(stream_id_for_script),
            '--output-path', video_full_path_for_script,  # Absoluter Pfad hier
            '--client-id', client_id,
            '--client-secret', client_secret,
            '--ffmpeg-path', ffmpeg_path,  # Übergebe Pfad an Skript
            '--streamlink-path', streamlink_path  # Übergebe Pfad an Skript
            # '--disable-ffmpeg' # Optional, falls du FFmpeg-Nachbearbeitung im Skript steuern willst
        ]
        print(f"INFO: Starte Hintergrund-Recorder-Prozess für Stream ID {stream_id_for_script}:")
        print(f"Kommando: {' '.join(command)}")

        try:
            process_creation_flags = 0
            if os.name == 'nt':  # Für Windows: Prozess in neuer Gruppe starten, um Ctrl+C vom Webserver zu entkoppeln
                process_creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

            # Starte den Prozess ohne auf sein Ende zu warten
            process = subprocess.Popen(command, creationflags=process_creation_flags)

            stream_obj.recorder_pid = process.pid
            stream_obj.analysis_status = 'RECORDING'  # Status auf "Nimmt auf"
            stream_obj.save(update_fields=['recorder_pid', 'analysis_status'])

            print(f"INFO: Recorder-Prozess gestartet (PID: {process.pid}). DB aktualisiert.")
            messages.success(request, f"Aufnahme für '{twitch_username}' (ID: {stream_id_for_script}) gestartet.")
        except Exception as e_popen:
            print(f"FEHLER beim Starten des Recorder-Prozesses: {e_popen}");
            traceback.print_exc()
            messages.error(request, "Fehler beim Starten des Aufnahme-Prozesses.")
            stream_obj.analysis_status = 'ERROR'  # Fehlerstatus setzen
            stream_obj.recorder_pid = None
            stream_obj.save(update_fields=['analysis_status', 'recorder_pid'])

        return redirect('index')
    else:  # GET Request
        messages.warning(request, "Ungültige Anfrage für Aufnahme.")
        return redirect('index')


# --- TWITCH AUFNAHME STOPPEN ---
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
        if stream_obj.analysis_status == 'RECORDING':  # Wenn Status noch auf Recording steht, aber PID fehlt
            stream_obj.analysis_status = 'ERROR'  # Fehler, da PID weg ist
            stream_obj.save(update_fields=['analysis_status'])
        return redirect('index')

    stop_successful = False
    process_was_found = True
    graceful_shutdown_wait_s = getattr(settings, 'RECORDER_GRACEFUL_SHUTDOWN_WAIT_S', 10)  # Aus Settings oder Default

    # 1. Versuch: Graceful Shutdown (SIGINT / CTRL_C_EVENT)
    print(
        log_prefix + f"Attempting graceful shutdown (Signal: SIGINT/CTRL_C_EVENT, Wait: {graceful_shutdown_wait_s}s)...")
    try:
        if os.name == 'nt':
            # Auf Windows ist CTRL_C_EVENT für Prozesse in neuer Gruppe oft nicht direkt per os.kill sendbar.
            # Besser ist, wenn das Skript selbst auf ein Flag reagiert oder eine andere IPC-Methode hat.
            # Als Fallback versuchen wir es, aber taskkill ist oft zuverlässiger.
            # Für Prozesse, die mit CREATE_NEW_PROCESS_GROUP gestartet wurden, ist es schwierig.
            # Alternativ könnte das Skript eine kleine Datei (Flag) überwachen, die bei Stop erstellt wird.
            # Hier wird taskkill /T bevorzugt. Wir versuchen es trotzdem mal.
            os.kill(pid_to_kill, signal.CTRL_C_EVENT)
        else:  # Unix-like
            os.kill(pid_to_kill, signal.SIGINT)

        print(log_prefix + "Signal sent. Waiting for process to terminate...")
        time.sleep(graceful_shutdown_wait_s)

        # Prüfen, ob Prozess noch läuft
        try:
            os.kill(pid_to_kill, 0)  # Sendet kein Signal, prüft nur Existenz
            print(log_prefix + f"Process {pid_to_kill} still exists after Signal. Proceeding to next stop method.")
        except OSError:  # Prozess nicht mehr da
            print(log_prefix + f"Process {pid_to_kill} successfully terminated after Signal.")
            stop_successful = True
            process_was_found = False
    except ProcessLookupError:  # Prozess schon weg
        print(log_prefix + f"Process {pid_to_kill} not found (ProcessLookupError). Already stopped or PID invalid.")
        stop_successful = True
        process_was_found = False
        messages.info(request, f"Aufnahme für Stream {stream_id} war bereits beendet oder PID ungültig.")
    except OSError as e_oskill:  # Fehler beim Senden des Signals (z.B. Permission Denied)
        print(log_prefix + f"OS-Error sending Signal or checking process: {e_oskill}. Trying next method.")
    except Exception as e_sig:  # Andere Fehler
        print(log_prefix + f"Unexpected error sending Signal: {e_sig}\n{traceback.format_exc()}")

    # 2. Versuch (Windows): taskkill (erst sanft, dann forciert)
    if not stop_successful and process_was_found and os.name == 'nt':
        print(log_prefix + "Attempting 'taskkill /PID ... /T' (graceful, includes child processes)...")
        try:
            # /T beendet auch Kindprozesse des PIDs (wichtig für streamlink, das ffmpeg starten kann)
            result = subprocess.run(['taskkill', '/PID', str(pid_to_kill), '/T'],
                                    capture_output=True, text=False, check=False, timeout=15)
            # stdout/stderr Dekodierung mit Fallback, falls Encoding-Probleme
            stderr_str = result.stderr.decode(sys.getfilesystemencoding(), errors='replace') if result.stderr else ""
            stdout_str = result.stdout.decode(sys.getfilesystemencoding(), errors='replace') if result.stdout else ""

            if result.returncode == 0:
                print(log_prefix + "Taskkill /T erfolgreich gesendet. Warte kurz auf Terminierung...")
                time.sleep(5)  # Kurze Wartezeit für Terminierung
                try:
                    os.kill(pid_to_kill, 0)  # Prüfe erneut
                except OSError:
                    stop_successful = True;
                    process_was_found = False
                    print(log_prefix + "Prozess erfolgreich mit taskkill /T beendet.")
                    messages.success(request, f"Aufnahme für Stream {stream_id} gestoppt.")
            elif "nicht gefunden" in stderr_str.lower() or "not found" in stderr_str.lower() or result.returncode == 128:
                print(
                    log_prefix + f"Prozess PID {pid_to_kill} nicht von taskkill /T gefunden. Vermutlich bereits beendet. Stderr: {stderr_str}")
                stop_successful = True;
                process_was_found = False
                messages.info(request, f"Aufnahme für Stream {stream_id} war bereits beendet (taskkill /T).")
            else:
                print(
                    log_prefix + f"Taskkill /T fehlgeschlagen. RC: {result.returncode}, Stderr: {stderr_str}, Stdout: {stdout_str}. Versuche Force Kill.")
        except subprocess.TimeoutExpired:
            print(log_prefix + "Taskkill /T Timeout. Versuche Force Kill.")
        except FileNotFoundError:  # taskkill nicht gefunden
            print(log_prefix + "FEHLER: taskkill Kommando nicht gefunden. Kann Prozess nicht sicher stoppen.");
            messages.error(request, "Systemfehler: 'taskkill' nicht gefunden. Aufnahme konnte nicht gestoppt werden.")
            # Status auf Fehler setzen, da wir nicht sicher sind, ob Aufnahme gestoppt wurde
            stream_obj.analysis_status = 'ERROR_STOP_FAILED'
        except Exception as e_taskkill_soft:
            print(log_prefix + f"Unerwarteter Fehler bei taskkill /T: {e_taskkill_soft}\n{traceback.format_exc()}")

    # 3. Versuch (Windows: taskkill /F oder Unix: SIGKILL) - Forciertes Beenden
    if not stop_successful and process_was_found:
        kill_cmd_str = ""
        kill_action_msg = ""
        try:
            if os.name == 'nt':
                kill_cmd_str = f"taskkill /F /PID {pid_to_kill} /T"
                kill_action_msg = "Aufnahme GEWALTSAM gestoppt (taskkill /F)."
                print(log_prefix + f"Attempting forceful '{kill_cmd_str}'...")
                result = subprocess.run(['taskkill', '/F', '/PID', str(pid_to_kill), '/T'],
                                        capture_output=True, text=False, check=False, timeout=10)
                stderr_str_force = result.stderr.decode(sys.getfilesystemencoding(),
                                                        errors='replace') if result.stderr else ""
                if result.returncode == 0:
                    stop_successful = True;
                    process_was_found = False
                elif "nicht gefunden" in stderr_str_force.lower() or "not found" in stderr_str_force.lower() or result.returncode == 128:
                    print(log_prefix + f"Prozess nicht von forceful taskkill gefunden. Stderr: {stderr_str_force}")
                    stop_successful = True;
                    process_was_found = False
                    kill_action_msg = "Aufnahme war bereits beendet (force kill)."  # Nachricht anpassen
                else:
                    print(
                        log_prefix + f"Force taskkill FEHLGESCHLAGEN. RC: {result.returncode}, Stderr: {stderr_str_force}")
                    kill_action_msg = "FEHLER beim gewaltsamen Stoppen der Aufnahme."
                    stream_obj.analysis_status = 'ERROR_STOP_FAILED'
            else:  # Unix-like
                kill_cmd_str = f"kill -9 {pid_to_kill}"
                kill_action_msg = "Aufnahme mit SIGKILL beendet."
                print(log_prefix + "Attempting forceful SIGKILL (Unix)...")
                os.kill(pid_to_kill, signal.SIGKILL)
                # SIGKILL sollte sofort wirken, kurze Pause bevor Prüfung
                time.sleep(1)
                try:  # Prüfen ob noch da
                    os.kill(pid_to_kill, 0)
                    # Wenn wir hier sind, hat SIGKILL nicht sofort gewirkt oder es gab ein Problem
                    print(log_prefix + "WARNUNG: Prozess existiert noch nach SIGKILL. Setze trotzdem auf erfolgreich.")
                    stop_successful = True;
                    process_was_found = False  # Annahme, dass es bald terminiert
                except OSError:  # Prozess ist weg
                    stop_successful = True;
                    process_was_found = False

            # Nachrichten basierend auf kill_action_msg
            if stop_successful and "GEWALTSAM" in kill_action_msg:
                messages.warning(request, kill_action_msg)
            elif stop_successful and "SIGKILL" in kill_action_msg:
                messages.warning(request, kill_action_msg)
            elif stop_successful and "bereits beendet" in kill_action_msg:
                messages.info(request, kill_action_msg)
            elif not stop_successful and "FEHLER" in kill_action_msg:
                messages.error(request, kill_action_msg)

        except ProcessLookupError:  # Prozess schon weg vor diesem Schritt
            print(log_prefix + f"Prozess nicht gefunden für {kill_cmd_str} (bereits beendet).")
            stop_successful = True;
            process_was_found = False
            if not messages.get_messages(request): messages.info(request,
                                                                 "Aufnahme war bereits beendet (vor force kill).")
        except subprocess.TimeoutExpired:
            print(log_prefix + f"Forceful {kill_cmd_str} timed out.")
            # Annahme, dass der Prozess bald terminiert oder nicht mehr reagiert
            stop_successful = True;
            process_was_found = False
            messages.warning(request, f"Timeout beim gewaltsamen Stoppen von {kill_cmd_str}. Status unklar.")
        except FileNotFoundError:  # taskkill nicht gefunden
            print(log_prefix + f"FEHLER: {kill_cmd_str.split()[0]} nicht gefunden.");
            messages.error(request, f"Systemfehler: '{kill_cmd_str.split()[0]}' nicht gefunden.");
            if stream_obj.analysis_status != 'ERROR_STOP_FAILED': stream_obj.analysis_status = 'ERROR_STOP_FAILED'
        except Exception as e_kill_force:
            print(
                log_prefix + f"Unerwarteter Fehler beim forcierten Stoppen: {e_kill_force}\n{traceback.format_exc()}");
            messages.error(request, "Unerwarteter Systemfehler beim Stoppen der Aufnahme.")
            if stream_obj.analysis_status != 'ERROR_STOP_FAILED': stream_obj.analysis_status = 'ERROR_STOP_FAILED'

    # Finale Statusaktualisierung für das Stream-Objekt
    stream_obj.recorder_pid = None  # PID entfernen
    final_status_for_stream = stream_obj.analysis_status  # Behalte Fehlerstatus, falls oben gesetzt

    if stop_successful or not process_was_found:  # Wenn Prozess gestoppt wurde oder nicht mehr da war
        video_file_valid = False
        if stream_obj.video_file and stream_obj.video_file.name:
            try:
                video_full_path_check = os.path.join(settings.MEDIA_ROOT, stream_obj.video_file.name)
                print(log_prefix + f"Überprüfe Videodatei: {video_full_path_check}")
                if os.path.exists(video_full_path_check) and os.path.getsize(
                        video_full_path_check) > 1024:  # Mindestgröße 1KB
                    video_file_valid = True
                    print(log_prefix + "Videodatei existiert und hat Inhalt.")
                else:
                    print(log_prefix + "Videodatei nicht gefunden oder leer.")
                    # Nicht unbedingt ein Fehler, wenn Aufnahme kurz war oder manuell abgebrochen wurde
                    # Aber wenn der Status noch 'RECORDING' war, ist es ein Problem.
            except Exception as e_path_check:
                print(log_prefix + f"Fehler beim Prüfen des Videodateipfads: {e_path_check}")

        if video_file_valid:
            # Wenn der Status noch auf RECORDING war, jetzt auf DOWNLOAD_COMPLETE setzen (oder äquivalent für lokale Aufnahmen)
            if final_status_for_stream == 'RECORDING' or final_status_for_stream == 'RECORDING_SCHEDULED':
                final_status_for_stream = 'DOWNLOAD_COMPLETE'  # Signalisiert, dass Video bereit zur Analyse ist
            # if not messages.get_messages(request): messages.success(request, f"Aufnahme für Stream {stream_id} erfolgreich beendet. Video gespeichert.")
            print(log_prefix + f"Aufnahme beendet. Videodatei '{stream_obj.video_file.name}' ist gültig.")
        else:  # Keine gültige Videodatei
            if final_status_for_stream not in ['ERROR', 'ERROR_NO_FILE', 'ERROR_STOP_FAILED']:
                final_status_for_stream = 'ERROR_NO_FILE'  # Datei fehlt nach Stop
            # if not messages.get_messages(request): messages.error(request, f"Aufnahme für Stream {stream_id} beendet, aber keine gültige Videodatei gefunden.")
            print(log_prefix + "Aufnahme beendet, aber keine gültige Videodatei gefunden.")

    elif final_status_for_stream not in ['ERROR', 'ERROR_STOP_FAILED', 'ERROR_NO_FILE']:  # Stop war nicht erfolgreich
        final_status_for_stream = 'ERROR_STOP_FAILED'
        print(log_prefix + "Stop-Kommando war nicht erfolgreich. Status auf ERROR_STOP_FAILED gesetzt.")
        # if not messages.get_messages(request): messages.error(request, f"Konnte Aufnahme für Stream {stream_id} nicht sicher stoppen.")

    stream_obj.analysis_status = final_status_for_stream
    stream_obj.save(update_fields=['analysis_status', 'recorder_pid'])
    print(log_prefix + f"Finaler DB Status: {stream_obj.analysis_status}. PID entfernt.")
    return redirect('index')


# --- CLIP HINZUFÜGEN (vermutlich ungenutzt) ---
def add_clip(request):
    print("WARNUNG: /add_clip/ aufgerufen (vermutlich ungenutzt).")
    return JsonResponse({'status': 'add_clip endpoint reached (likely unused)'})


# --- STREAM DETAILSEITE (Highlights Anzeige) ---
@login_required
def stream(request, stream_id):
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    # Bestelle nach start_time, falls vorhanden, ansonsten nach ID (Erstellungsreihenfolge)
    clips_data = StreamHighlight.objects.filter(
        user_id=request.user.username,
        stream_link=stream_obj.stream_link  # Wichtig für korrekte Zuordnung der Highlights
    ).order_by('start_time', 'id')  # Sortiere nach Startzeit, dann nach ID

    for clip in clips_data:
        # Stelle sicher, dass media_url korrekt gebildet wird
        if clip.clip_link:
            if not clip.clip_link.startswith(('http://', 'https://', '/')):  # Relative Pfade aus MEDIA_ROOT
                try:
                    # clip.clip_link sollte sein: 'uploads/user/stream_id/highlights/clip.mp4'
                    clip.media_url = os.path.join(settings.MEDIA_URL, clip.clip_link).replace('\\', '/')
                except Exception as e_media_url:
                    clip.media_url = None
                    print(f"Fehler beim Erstellen der Media URL für Clip {clip.id}: {e_media_url}")
            else:  # Bereits absolute URL oder /media/... Pfad
                clip.media_url = clip.clip_link
        else:
            clip.media_url = None

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
    log_prefix = f"[Delete Stream {stream_id}] "
    print(f"{log_prefix}Versuche Stream '{stream_title_for_log}' (User: {request.user.username}) zu löschen.")

    # Pfad zum Stream-Verzeichnis (enthält Video, Sound-CSV, Chat-Log, Highlights)
    stream_dir_relative = None
    if stream_obj.video_file and stream_obj.video_file.name:
        # Annahme: video_file.name ist 'uploads/user_id/stream_id/video.mp4'
        # Das Verzeichnis ist dann 'uploads/user_id/stream_id/'
        stream_dir_relative = os.path.dirname(stream_obj.video_file.name)
    elif stream_obj.chat_log_file and stream_obj.chat_log_file:  # Falls kein Video aber Chat-Log da ist
        stream_dir_relative = os.path.dirname(stream_obj.chat_log_file)

    stream_dir_absolute_to_delete = None
    if stream_dir_relative:
        # Stelle sicher, dass der Pfad dem erwarteten Muster entspricht, um versehentliches Löschen zu vermeiden
        expected_pattern = os.path.join('uploads', str(stream_obj.user_id), str(stream_obj.id)).replace('\\', '/')
        if stream_dir_relative.startswith(expected_pattern):
            stream_dir_absolute_to_delete = os.path.join(settings.MEDIA_ROOT, stream_dir_relative)
            print(f"{log_prefix}Stream-Verzeichnis zur Löschung bestimmt: {stream_dir_absolute_to_delete}")
        else:
            print(
                f"{log_prefix}WARNUNG: Stream-Verzeichnispfad '{stream_dir_relative}' entspricht nicht dem erwarteten Muster '{expected_pattern}'. Verzeichnis wird nicht gelöscht.")

    # Lösche zugehörige Highlight-Einträge aus der Datenbank
    highlights_deleted_count = \
    StreamHighlight.objects.filter(user_id=stream_obj.user_id, stream_link=stream_obj.stream_link).delete()[0]
    print(
        f"{log_prefix}{highlights_deleted_count} Highlight-DB-Einträge gelöscht für Stream-Link: '{stream_obj.stream_link}'.")

    # Lösche das Stream-Objekt selbst aus der Datenbank
    stream_obj.delete()
    print(f"{log_prefix}Stream DB-Eintrag (ID: {stream_id}) gelöscht.")

    # Physische Dateien und Verzeichnisse löschen
    deleted_physical_dir = False
    if stream_dir_absolute_to_delete and os.path.exists(stream_dir_absolute_to_delete) and os.path.isdir(
            stream_dir_absolute_to_delete):
        # Sicherheitsprüfung: Nicht MEDIA_ROOT selbst löschen oder Pfade außerhalb davon
        norm_media_root = os.path.normpath(settings.MEDIA_ROOT)
        norm_dir_to_delete = os.path.normpath(stream_dir_absolute_to_delete)
        if norm_dir_to_delete != norm_media_root and norm_media_root in norm_dir_to_delete:
            try:
                shutil.rmtree(stream_dir_absolute_to_delete)
                print(f"{log_prefix}Stream-Verzeichnis erfolgreich gelöscht: {stream_dir_absolute_to_delete}")
                messages.success(request,
                                 f"Stream '{stream_title_for_log}' und alle zugehörigen Dateien/Verzeichnisse gelöscht.")
                deleted_physical_dir = True
            except OSError as e_rmdir:
                print(
                    f"{log_prefix}FEHLER beim Löschen des Stream-Verzeichnisses {stream_dir_absolute_to_delete}: {e_rmdir}")
                messages.error(request,
                               f"Stream-Datenbankeintrag gelöscht, aber das Verzeichnis konnte nicht entfernt werden: {e_rmdir}")
        else:
            print(
                f"{log_prefix}SICHERHEITSABBRUCH: Löschen von Verzeichnis '{stream_dir_absolute_to_delete}' verhindert.")
            messages.warning(request,
                             "Stream-Datenbankeintrag gelöscht, aber das Hauptverzeichnis wurde aus Sicherheitsgründen nicht entfernt.")

    if not deleted_physical_dir and not messages.get_messages(
            request):  # Wenn Verzeichnis nicht gelöscht werden konnte/sollte
        messages.success(request, f"Stream '{stream_title_for_log}' (Datenbankeintrag) gelöscht.")

    print(f"{log_prefix}Löschvorgang abgeschlossen für ehemaligen Stream ID: {stream_id}")
    return redirect('index')


# --- VIDEOPLAYER SEITE ---
@login_required
def video_player_view(request, stream_id):
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    video_url = None
    video_exists = False
    error_message = None
    log_prefix = f"[VideoPlayer View Stream {stream_id}] "

    print(
        f"{log_prefix}Status: {stream_obj.analysis_status}, Video File: {stream_obj.video_file.name if stream_obj.video_file else 'N/A'}")

    if stream_obj.video_file and stream_obj.video_file.name:
        try:
            # Prüfe zuerst, ob die Datei physisch existiert
            absolute_video_path = os.path.join(settings.MEDIA_ROOT, stream_obj.video_file.name)
            if os.path.exists(absolute_video_path):
                video_url = stream_obj.video_file.url  # Django's URL-Mechanismus
                video_exists = True
                print(f"{log_prefix}Video-URL erfolgreich erhalten: {video_url}")
            else:
                print(
                    f"{log_prefix}FEHLER: Videodatei in DB vermerkt, aber nicht im Dateisystem gefunden: {absolute_video_path}")
                error_message = "Fehler: Videodatei nicht im Dateisystem vorhanden."
                stream_obj.analysis_status = 'ERROR_NO_FILE'  # Korrigiere Status, falls inkonsistent
                stream_obj.save(update_fields=['analysis_status'])
        except Exception as e_url:
            print(f"{log_prefix}FEHLER beim Zugriff auf video_file.url oder Dateiprüfung: {e_url}")
            traceback.print_exc()
            error_message = "Fehler beim Abrufen der Video-URL oder Validieren der Datei."
            video_exists = False  # Sicherstellen
            video_url = None
    else:  # Kein video_file oder video_file.name im DB-Objekt
        print(log_prefix + "video_file.name ist nicht gesetzt oder video_file ist None.")
        status_map = {
            'RECORDING': "Video wird noch aufgenommen.",
            'PENDING': "Video wird gerade verarbeitet oder Upload unvollständig.",
            'PROCESSING': "Video wird gerade verarbeitet.",
            'RECORDING_SCHEDULED': "Aufnahme ist geplant.",
            'DOWNLOAD_SCHEDULED': "Download ist geplant.",
            'DOWNLOADING': "Video lädt herunter.",
            'MANUALLY_STOPPED': "Aufnahme manuell gestoppt. Verarbeitung fehlt oder fehlgeschlagen.",
            'ERROR': "Fehler bei Aufnahme/Verarbeitung.",
            'ERROR_NO_FILE': "Fehler: Videodatei nicht gefunden.",
            'ERROR_STOP_FAILED': "Fehler: Aufnahme konnte nicht gestoppt werden.",
            'ERROR_DOWNLOAD': "Fehler beim Download.",
            'ERROR_DOWNLOAD_TIMEOUT': "Download Timeout."
        }
        error_message = status_map.get(stream_obj.analysis_status,
                                       "Kein Videopfad gespeichert oder unbekannter Status.")

    # Fallback und Konsistenzprüfung
    if not video_exists and stream_obj.video_file and stream_obj.video_file.name and not error_message:
        # Dieser Block ist redundant, wenn die obere Prüfung schon os.path.exists macht.
        # Er dient als zusätzliche Sicherheit.
        abs_path_check = os.path.join(settings.MEDIA_ROOT, stream_obj.video_file.name)
        if os.path.exists(abs_path_check):
            print(f"{log_prefix}Fallback: Datei existiert bei {abs_path_check}. Konstruiere URL manuell.")
            video_url = os.path.join(settings.MEDIA_URL, stream_obj.video_file.name).replace('\\', '/')
            video_exists = True
            if not error_message: error_message = "Video-URL manuell konstruiert nach Validierung."  # Selten
        # else: # Bereits oben abgedeckt
        # if not error_message: error_message = "Videodatei nicht im System gefunden (Fallback-Prüfung)."

    if stream_obj.analysis_status == 'COMPLETE' and not video_exists and not error_message:
        error_message = "Analyse abgeschlossen, aber Video nicht ladbar. Datei evtl. entfernt oder Pfadproblem."
        print(f"{log_prefix}Inkonsistenz: Status COMPLETE, aber Video nicht auffindbar (video_exists=False).")

    context = {
        'stream': stream_obj,
        'name': request.user.username,
        'is_staff': request.user.is_staff,
        'video_url': video_url,
        'video_exists': video_exists,
        'error_message': error_message
    }
    return render(request, 'viewer/video_player.html', context)


# --- GENERATOR SEITE (Anzeige von Analyse-Stats und Highlight-Optionen) ---
@login_required
def generator_view(request, stream_id):
    stream_obj = get_object_or_404(Stream, id=stream_id, user_id=request.user.username)
    clips_data = StreamHighlight.objects.none()  # Default
    error_message_highlights = None
    log_prefix = f"[Generator View Stream {stream_id}] "

    print(f"{log_prefix}Stream-Link für Highlights: '{stream_obj.stream_link}'")
    try:
        # Lade die bereits generierten Highlights für diesen Stream
        clips_data = StreamHighlight.objects.filter(
            user_id=request.user.username,
            stream_link=stream_obj.stream_link  # Stellt sicher, dass es Highlights für diesen spezifischen Stream sind
        ).order_by('start_time')  # Sortiere nach Startzeit

        print(f"{log_prefix}Gefunden: {clips_data.count()} Highlights für Stream-Link '{stream_obj.stream_link}'.")
        for clip in clips_data:
            if clip.clip_link and not clip.clip_link.startswith(('http://', 'https://', '/')):
                try:
                    clip.media_url = os.path.join(settings.MEDIA_URL, clip.clip_link).replace('\\', '/')
                except Exception:  # Falls Pfad ungültig etc.
                    clip.media_url = None  # Setze auf None, damit Template es behandeln kann
            else:  # Bereits absolute URL oder /media/... Pfad
                clip.media_url = clip.clip_link
    except FieldError as e_filter:  # Fehler beim Sortieren oder Filtern
        print(f"{log_prefix}FieldError beim Laden der Highlights: {e_filter}")
        traceback.print_exc()
        error_message_highlights = f"Fehler beim Laden/Sortieren der Highlights: {e_filter}."
        # Versuche, zumindest die ungeordneten Daten zu laden
        try:
            clips_data = StreamHighlight.objects.filter(user_id=request.user.username,
                                                        stream_link=stream_obj.stream_link)
        except:  # Generischer Fallback
            clips_data = StreamHighlight.objects.none()
    except Exception as e_clips:  # Unerwarteter Fehler
        print(f"{log_prefix}Unerwarteter Fehler beim Laden der Highlights: {e_clips}")
        traceback.print_exc()
        error_message_highlights = "Unerwarteter Fehler beim Laden der gespeicherten Highlights."

    # Default-Threshold für das Formular (falls vorhanden)
    current_form_threshold = None
    if stream_obj.p95_loudness is not None and stream_obj.p95_loudness > 0:
        current_form_threshold = stream_obj.p95_loudness
    elif stream_obj.p90_loudness is not None and stream_obj.p90_loudness > 0:
        current_form_threshold = stream_obj.p90_loudness

    current_form_threshold = max(0.001,
                                 current_form_threshold) if current_form_threshold is not None else 0.01  # Fallback

    context = {
        'stream': stream_obj,
        'name': request.user.username,
        'is_staff': request.user.is_staff,
        'clips_data': clips_data,
        'current_threshold': f"{current_form_threshold:.4f}",  # Formatiert für Input-Feld
        'error_message_highlights': error_message_highlights
    }
    return render(request, 'viewer/generator.html', context)


# --- HIGHLIGHTS NEU GENERIEREN (Momentan deaktiviert) ---
@login_required
@require_POST
def regenerate_highlights_view(request, stream_id):
    # Diese Funktion ist laut deinem Originalcode deaktiviert.
    # Wenn du sie aktivieren willst, müsstest du hier eine ähnliche Logik
    # wie in `generate_highlights_view` implementieren, aber ggf. mit
    # neuen Parametern (z.B. Thresholds) aus dem Request-Formular.
    messages.warning(request, "Neugenerierung von Highlights ist derzeit deaktiviert.")
    return redirect('generator', stream_id=stream_id)


# --- NEUE VIEWS FÜR TWITCH VOD IMPORT ---
@login_required
def fetch_twitch_vods_view(request):
    twitch_vods_results = []  # Umbenannt, um Verwechslung mit session var zu vermeiden
    searched_channel_name_input = ""  # Für das Formularfeld

    if request.method == 'POST':
        searched_channel_name_input = request.POST.get('twitch_channel_name', '').strip()
        request.session['search_attempted_context'] = True  # Markiere, dass gesucht wurde

        if not searched_channel_name_input:
            messages.error(request, "Bitte einen Twitch Kanalnamen eingeben.")
            request.session['twitch_vods_context'] = []
            request.session['searched_channel_name_context'] = ''
            return redirect('index')

        print(f"[Fetch VODs View] User '{request.user.username}' sucht VODs für: {searched_channel_name_input}")
        user_id_from_twitch = twitch_api_client.get_user_id_by_login(searched_channel_name_input)

        if user_id_from_twitch:
            print(f"[Fetch VODs View] Gefundene User ID: {user_id_from_twitch} für {searched_channel_name_input}")
            # Hole mehr VODs, z.B. die letzten 12 (max_results anpassen)
            fetched_vods_data = twitch_api_client.get_user_vods(user_id_from_twitch, max_results=12)
            if fetched_vods_data:
                print(f"[Fetch VODs View] {len(fetched_vods_data)} VODs gefunden.")
                twitch_vods_results = fetched_vods_data  # Ergebnisse für Template
            else:
                print(
                    f"[Fetch VODs View] Keine VODs für {searched_channel_name_input} (User ID: {user_id_from_twitch}).")
                messages.info(request, f"Keine VODs für '{searched_channel_name_input}' gefunden.")
        else:
            print(f"[Fetch VODs View] Konnte User ID für {searched_channel_name_input} nicht finden.")
            messages.error(request, f"Kanal '{searched_channel_name_input}' nicht gefunden oder API-Fehler.")

        # Speichere Ergebnisse und Suchbegriff in der Session für die Anzeige auf der Index-Seite
        request.session['twitch_vods_context'] = twitch_vods_results
        request.session['searched_channel_name_context'] = searched_channel_name_input
    else:  # GET Request oder wenn keine POST-Daten
        request.session['twitch_vods_context'] = []
        request.session['searched_channel_name_context'] = ''
        request.session['search_attempted_context'] = False  # Zurücksetzen, wenn nicht gesucht wurde

    return redirect('index')


@login_required
@require_POST  # Import sollte POST sein
def import_selected_twitch_vod_view(request, vod_id):
    log_prefix_outer = f"[Import VOD View, VOD ID: {vod_id}] "

    # Hole Daten aus dem POST-Request (vom Formular in main.html)
    vod_title = request.POST.get('vod_title', f"Twitch VOD {vod_id}")
    vod_url = request.POST.get('vod_url')  # Die URL des VODs selbst
    twitch_channel_name = request.POST.get('twitch_channel_name', '').strip().lower()  # Der Kanalname

    if not vod_url:
        messages.error(request, "VOD URL nicht übermittelt. Import fehlgeschlagen.");
        print(log_prefix_outer + "FEHLER: VOD URL fehlt im POST Request.");
        return redirect('index')
    if not twitch_channel_name:  # Wichtig für Stream.stream_link
        messages.error(request, "Twitch Kanalname fehlt. Import fehlgeschlagen.");
        print(log_prefix_outer + "FEHLER: Twitch Kanalname fehlt im POST Request.");
        return redirect('index')

    print(
        f"{log_prefix_outer}User '{request.user.username}' importiert '{vod_title}' (URL: {vod_url}, Kanal: {twitch_channel_name})")

    # Prüfe, ob dieses VOD bereits importiert wird oder wurde (basierend auf twitch_vod_id)
    existing_stream = Stream.objects.filter(twitch_vod_id=vod_id, user_id=request.user.username).first()
    if existing_stream:
        messages.warning(request, f"VOD '{vod_title}' (ID: {vod_id}) wurde bereits importiert oder der Import läuft.")
        print(
            f"{log_prefix_outer}WARNUNG: VOD ID {vod_id} existiert bereits für User {request.user.username} (Stream ID: {existing_stream.id})")
        return redirect('index')

    try:
        stream_obj = Stream.objects.create(
            user_id=request.user.username,
            stream_link=twitch_channel_name,  # Wichtig für die Zuordnung von Highlights etc.
            stream_name=vod_title[:198],  # Kürze Titel falls zu lang für DB-Feld
            analysis_status='DOWNLOAD_SCHEDULED',  # Status, dass Download geplant ist
            twitch_vod_id=vod_id  # Speichere die Twitch VOD ID
        )
        stream_id_for_thread = stream_obj.id  # ID des neu erstellten Stream-Objekts
        print(f"{log_prefix_outer}Stream-Objekt ID {stream_id_for_thread} für VOD-Import erstellt.")
    except Exception as e_db_create:
        print(f"{log_prefix_outer}FEHLER beim Erstellen des Stream-Objekts für VOD-Import: {e_db_create}");
        traceback.print_exc()
        messages.error(request, "Datenbankfehler beim Erstellen des Stream-Eintrags für den VOD-Import.");
        return redirect('index')

    # Pfade für den Download erstellen
    video_full_path_for_download = None
    try:
        user_id_part = str(request.user.username)
        stream_id_part = str(stream_id_for_thread)
        output_filename = f"{stream_id_part}.mp4"  # Standard-Dateiname für das Video

        relative_dir = os.path.join('uploads', user_id_part, stream_id_part)
        absolute_target_dir = os.path.join(settings.MEDIA_ROOT, relative_dir)
        os.makedirs(absolute_target_dir, exist_ok=True)  # Erstelle Verzeichnis falls nicht vorhanden

        video_full_path_for_download = os.path.join(absolute_target_dir, output_filename)
        relative_file_path_for_db = os.path.join(relative_dir, output_filename).replace('\\', '/')

        stream_obj.video_file.name = relative_file_path_for_db  # Setze den Pfad im Modellfeld
        stream_obj.save(update_fields=['video_file'])  # Speichere die Pfadänderung

        print(f"{log_prefix_outer}Zielpfad für VOD-Download: {video_full_path_for_download}")
        print(f"{log_prefix_outer}DB video_file.name gesetzt auf: {stream_obj.video_file.name}")
    except Exception as e_path:
        print(f"{log_prefix_outer}FEHLER beim Erstellen der Downloadpfade: {e_path}");
        traceback.print_exc()
        messages.error(request, "Pfadfehler beim Vorbereiten des VOD-Imports.");
        if stream_obj: stream_obj.delete()  # Lösche das fehlerhafte Stream-Objekt
        return redirect('index')

    # Starte den Download-Thread
    try:
        print(f"{log_prefix_outer}Starte Download-Thread für Stream ID: {stream_id_for_thread}, VOD URL: {vod_url}")
        download_thread = threading.Thread(
            target=run_vod_download_and_analysis_thread,  # Die angepasste Funktion
            args=(vod_url, video_full_path_for_download, stream_id_for_thread, request.user.username),
            daemon=True  # Thread stirbt, wenn Hauptprozess beendet wird
        )
        download_thread.start()
        print(f"{log_prefix_outer}Download-Thread erfolgreich gestartet.");
        messages.success(request, f"Import und Download für '{vod_title}' gestartet. Dies kann einige Zeit dauern.")
    except Exception as e_thread:
        print(f"{log_prefix_outer}FEHLER beim Starten des Download-Threads: {e_thread}");
        traceback.print_exc()
        stream_obj.analysis_status = 'ERROR'  # Fehlerstatus setzen
        stream_obj.save(update_fields=['analysis_status'])
        messages.error(request, "Fehler beim Starten des VOD-Importprozesses.")

    return redirect('index')


# --- NEUE run_vod_download_and_analysis_thread ---
def run_vod_download_and_analysis_thread(vod_url, target_video_path, stream_id,
                                         user_name):  # user_name für Logging/Pfade
    thread_id = threading.get_ident()
    log_prefix = f"[VOD Download Thread-{thread_id}, StreamID: {stream_id}] "
    print(f"\n{log_prefix}--- Thread gestartet für VOD URL: {vod_url}, Ziel-Videopfad: {target_video_path} ---")

    start_time_thread = time.time()
    stream_obj = None
    video_download_successful = False  # Geändert von download_successful
    streamlink_process = None
    repack_process = None
    chat_log_relative_path_for_db = None  # Für Speicherung im DB-Modell

    try:
        # Hole das Stream-Objekt erneut, um sicherzustellen, dass es aktuell ist
        stream_obj = Stream.objects.get(id=stream_id)
        stream_obj.analysis_status = 'DOWNLOADING'
        stream_obj.save(update_fields=['analysis_status'])

        # Pfade zu externen Tools
        streamlink_path = getattr(settings, 'STREAMLINK_PATH', 'streamlink')
        ffmpeg_path = getattr(settings, 'FFMPEG_PATH', 'ffmpeg')
        # Qualität könnte auch aus Settings oder Stream-Objekt kommen
        download_quality = getattr(settings, 'DEFAULT_VOD_DOWNLOAD_QUALITY', "480p")

        # Streamlink-Kommando für Video-Download
        streamlink_cmd = [
            streamlink_path,
            "--ffmpeg-ffmpeg", ffmpeg_path,
            "--twitch-disable-ads",
            "--force",  # Überschreibt existierende Datei, falls vorhanden
            vod_url,
            download_quality,
            "-o", target_video_path
        ]
        print(f"{log_prefix}Führe Streamlink aus (Qualität '{download_quality}'): {' '.join(streamlink_cmd)}")

        streamlink_process = subprocess.Popen(streamlink_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                              encoding='utf-8')
        streamlink_timeout = getattr(settings, 'STREAMLINK_TIMEOUT', 10800)  # Default 3 Stunden
        sl_stdout, sl_stderr = streamlink_process.communicate(timeout=streamlink_timeout)

        if streamlink_process.returncode == 0 and os.path.exists(target_video_path) and os.path.getsize(
                target_video_path) > 1000:  # Mindestgröße 1KB
            print(f"{log_prefix}Streamlink Video-Download erfolgreich: {target_video_path}")

            # Optional: FFmpeg Repack für bessere Kompatibilität / Faststart
            original_extension = os.path.splitext(target_video_path)[1].lower() or ".mp4"
            repacked_video_path_temp = target_video_path + ".repacked_temp" + original_extension  # Temporärer Name

            ffmpeg_repack_cmd = [
                ffmpeg_path, '-i', target_video_path,
                '-c', 'copy',  # Kopiert Codecs, kein Neukodieren
                '-movflags', '+faststart',  # Wichtig für Web-Streaming
                repacked_video_path_temp, "-y"  # Überschreibe temporäre Datei falls vorhanden
            ]
            print(f"{log_prefix}Führe FFmpeg Repack aus: {' '.join(ffmpeg_repack_cmd)}")
            try:
                repack_process = subprocess.Popen(ffmpeg_repack_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                  text=True, encoding='utf-8')
                ffmpeg_repack_timeout = getattr(settings, 'FFMPEG_REPACK_TIMEOUT', 1800)  # Default 30 Min
                repack_stdout, repack_stderr = repack_process.communicate(timeout=ffmpeg_repack_timeout)

                if repack_process.returncode == 0 and os.path.exists(repacked_video_path_temp):
                    shutil.move(repacked_video_path_temp, target_video_path)  # Ersetze Original mit repacked Version
                    print(f"{log_prefix}FFmpeg Repack erfolgreich. Originalvideo ersetzt: {target_video_path}")
                    video_download_successful = True
                else:
                    print(
                        f"{log_prefix}FFmpeg Repack FEHLGESCHLAGEN. RC: {repack_process.returncode if repack_process else 'N/A'}")
                    if repack_stderr: print(f"  FFmpeg Repack Stderr: {repack_stderr.strip()}")
                    if os.path.exists(repacked_video_path_temp): os.remove(
                        repacked_video_path_temp)  # Lösche temp Datei
                    video_download_successful = True  # Video-Download war ja erfolgreich, nur Repack nicht
                    print(f"{log_prefix}WARNUNG: Repack fehlgeschlagen, Originalvideo wird verwendet.")
            except Exception as e_repack:
                print(f"{log_prefix}FEHLER während FFmpeg Repack: {e_repack}")
                traceback.print_exc()
                if repack_process and repack_process.poll() is None: repack_process.kill(); repack_process.communicate()
                if os.path.exists(repacked_video_path_temp): os.remove(repacked_video_path_temp)
                video_download_successful = True  # Video-Download war ja erfolgreich
                print(f"{log_prefix}WARNUNG: Repack-Exception, Originalvideo wird verwendet.")

            if video_download_successful:  # Setze Status nur wenn Repack auch durchlief oder übersprungen wurde
                stream_obj.analysis_status = 'DOWNLOAD_COMPLETE'

        else:  # Streamlink Download fehlgeschlagen
            print(
                f"{log_prefix}Streamlink Video-Download FEHLGESCHLAGEN oder Datei ungültig. RC: {streamlink_process.returncode if streamlink_process else 'N/A'}")
            if sl_stderr: print(f"  Streamlink Stderr: {sl_stderr.strip()}")
            stream_obj.analysis_status = 'ERROR_DOWNLOAD'
            # Hier Thread beenden, da kein Video für Chat-Download vorhanden ist
            if stream_obj: stream_obj.save(update_fields=['analysis_status'])
            end_time_thread = time.time()
            print(
                f"{log_prefix}--- Thread beendet aufgrund Video-Download-Fehler (Dauer: {time.strftime('%H:%M:%S', time.gmtime(end_time_thread - start_time_thread))}) ---\n")
            return

        # --- Chat-Log herunterladen NACH erfolgreichem Video-Download ---
        if video_download_successful:
            # Pfad-Konstruktion für Chat-Log
            video_dir_name = os.path.basename(os.path.dirname(target_video_path))  # Sollte stream_id sein
            user_dir_name = os.path.basename(
                os.path.dirname(os.path.dirname(target_video_path)))  # Sollte user_id/username sein

            chat_log_filename = f"{stream_id}_chat.json"  # Konsistenter Dateiname
            chat_log_absolute_path = os.path.join(os.path.dirname(target_video_path), chat_log_filename)
            chat_log_relative_path_for_db = os.path.join('uploads', user_dir_name, video_dir_name,
                                                         chat_log_filename).replace('\\', '/')

            try:
                print(f"{log_prefix}Starte Download des Chat-Logs für VOD URL: {vod_url} nach {chat_log_absolute_path}")
                # chat-downloader Kommando
                # Stelle sicher, dass chat-downloader im PATH ist oder gib den vollen Pfad an.
                # chat_downloader_exe = getattr(settings, 'CHAT_DOWNLOADER_PATH', 'chat_downloader')
                chat_download_cmd = [
                    "chat_downloader",  # Oder chat_downloader_exe
                    vod_url,  # Kann URL oder VOD ID sein
                    "--output", chat_log_absolute_path,

                    # '--message_types', 'message spectator system_message', # Optional: Mehr Nachrichtentypen
                    # '--message_groups', 'messages', # Fokussiert auf Nachrichten
                    # '--interrupt_after', '0' # Kein automatischer Abbruch durch chat-downloader
                ]
                chat_download_timeout = getattr(settings, 'CHAT_DOWNLOAD_TIMEOUT', 3600)  # 1 Stunde Timeout

                print(f"{log_prefix}Führe Chat-Download aus: {' '.join(chat_download_cmd)}")
                chat_proc = subprocess.Popen(chat_download_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                             text=True, encoding='utf-8')
                chat_stdout, chat_stderr = chat_proc.communicate(timeout=chat_download_timeout)

                if chat_proc.returncode == 0 and os.path.exists(chat_log_absolute_path) and os.path.getsize(
                        chat_log_absolute_path) > 0:
                    print(f"{log_prefix}Chat-Log erfolgreich heruntergeladen: {chat_log_absolute_path}")
                    # chat_log_relative_path_for_db ist bereits gesetzt
                elif os.path.exists(chat_log_absolute_path) and os.path.getsize(chat_log_absolute_path) == 0:
                    print(
                        f"{log_prefix}WARNUNG: Chat-Log wurde heruntergeladen, ist aber leer: {chat_log_absolute_path}. VOD hatte möglicherweise keinen Chat oder das Tool hat nichts extrahiert.")
                    os.remove(chat_log_absolute_path)  # Leere Datei entfernen
                    chat_log_relative_path_for_db = None  # Kein Pfad zu leerer Datei speichern
                else:
                    print(
                        f"{log_prefix}FEHLER beim Download des Chat-Logs. RC: {chat_proc.returncode if chat_proc else 'N/A'}")
                    if chat_stderr: print(f"  Chat Downloader Stderr: {chat_stderr.strip()}")
                    if chat_stdout: print(
                        f"  Chat Downloader Stdout: {chat_stdout.strip()}")  # Fehler sind manchmal hier
                    chat_log_relative_path_for_db = None
            except subprocess.TimeoutExpired:
                print(f"{log_prefix}Timeout ({chat_download_timeout}s) beim Download des Chat-Logs.")
                if 'chat_proc' in locals() and chat_proc.poll() is None: chat_proc.kill(); chat_proc.communicate()
                chat_log_relative_path_for_db = None
            except FileNotFoundError:  # Falls chat_downloader nicht gefunden wird
                print(
                    f"{log_prefix}FEHLER: 'chat_downloader' Kommando nicht gefunden. Chat kann nicht heruntergeladen werden.")
                chat_log_relative_path_for_db = None
            except Exception as e_chat_dl:
                print(f"{log_prefix}Unerwarteter Fehler beim Chat-Log Download: {e_chat_dl}")
                traceback.print_exc()
                chat_log_relative_path_for_db = None

    except subprocess.TimeoutExpired as e_timeout_main:  # Timeout für Streamlink
        print(f"{log_prefix}Streamlink Download TIMEOUT ({streamlink_timeout}s). VOD URL: {vod_url}")
        if streamlink_process and streamlink_process.poll() is None: streamlink_process.kill(); streamlink_process.communicate()
        if stream_obj: stream_obj.analysis_status = 'ERROR_DOWNLOAD_TIMEOUT'
        video_download_successful = False  # Sicherstellen
    except Exception as e_dl_outer_main:  # Genereller Fehler im Thread
        print(f"{log_prefix}KRITISCHER FEHLER im VOD Download/Repack Thread: {e_dl_outer_main}")
        traceback.print_exc()
        if stream_obj: stream_obj.analysis_status = 'ERROR_DOWNLOAD'
        video_download_successful = False  # Sicherstellen

    # --- Finales Speichern des Stream-Objekts ---
    if stream_obj:
        current_status = stream_obj.analysis_status  # Behalte Status wie DOWNLOAD_COMPLETE oder ERROR_DOWNLOAD
        update_fields_list = ['analysis_status']  # analysis_status wird immer aktualisiert

        if hasattr(stream_obj, 'chat_log_file'):  # Nur wenn das Feld im Modell existiert
            if chat_log_relative_path_for_db:
                stream_obj.chat_log_file = chat_log_relative_path_for_db
            else:  # Wenn Chat-Download fehlschlug oder kein Chat da war
                stream_obj.chat_log_file = None  # Stelle sicher, dass es None ist
            update_fields_list.append('chat_log_file')

        # Setze finalen Status basierend auf Erfolg
        if video_download_successful and current_status != 'ERROR_DOWNLOAD':  # Wenn Video da ist und kein Fehler vorher
            stream_obj.analysis_status = 'DOWNLOAD_COMPLETE'
        elif not video_download_successful and current_status not in ['ERROR_DOWNLOAD', 'ERROR_DOWNLOAD_TIMEOUT']:
            # Fallback, falls video_download_successful fälschlicherweise nicht gesetzt wurde, aber Status OK ist.
            # Oder wenn ein Fehler nach erfolgreichem Download auftrat (z.B. beim Chat-Download)
            if stream_obj.analysis_status == 'DOWNLOADING':  # Wenn immer noch auf Downloading steht
                stream_obj.analysis_status = 'ERROR_DOWNLOAD'  # Setze Fehler, wenn nicht explizit erfolgreich

        stream_obj.save(update_fields=update_fields_list)
        print(
            f"{log_prefix}Stream-Objekt gespeichert. Status: {stream_obj.analysis_status}, Chat-Log: {stream_obj.chat_log_file or 'Nicht vorhanden'}")

    if video_download_successful:
        print(
            f"{log_prefix}Video-Download und ggf. Chat-Log-Download abgeschlossen. Stream-Status: {stream_obj.analysis_status if stream_obj else 'N/A'}.")
    else:
        print(
            f"{log_prefix}Video-Download fehlgeschlagen. Stream-Status: {stream_obj.analysis_status if stream_obj else 'N/A'}.")

    end_time_thread = time.time()
    duration_formatted = time.strftime('%H:%M:%S', time.gmtime(end_time_thread - start_time_thread))
    print(f"{log_prefix}--- Thread beendet (Dauer: {duration_formatted}) ---\n")