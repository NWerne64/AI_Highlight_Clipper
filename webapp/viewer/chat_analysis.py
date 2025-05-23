# webapp/viewer/chat_analysis.py
import os
import json
import pandas as pd
from datetime import datetime, timedelta
from django.conf import settings


# from transformers import pipeline # Wird in views.py importiert und übergeben

class ChatAnalyzerFromFile:
    def __init__(self, stream_features_df, stream_id_str, classifier_pipeline):
        self.stream_features = stream_features_df  # Initial leeres DataFrame
        self.stream_id_str = stream_id_str  # Stream ID als String für Dateinamen
        self.classifier = classifier_pipeline
        self.seconds_interval = 10  # 10-Sekunden-Fenster

    def parse_chat_log(self, chat_log_path):
        """
        Parst eine heruntergeladene Chat-Log-Datei (JSON-Format von chat-downloader).
        Gibt eine Liste von Dictionaries {'time_offset_seconds': float, 'text': str} zurück.
        """
        messages = []
        try:
            with open(chat_log_path, 'r', encoding='utf-8') as f:
                # chat-downloader gibt typischerweise eine Liste von JSON-Objekten (eines pro Zeile)
                # oder ein einzelnes JSON-Array aus.
                # Wir versuchen beides.
                try:
                    # Versuche, die gesamte Datei als einzelnes JSON-Array zu laden
                    chat_data_list = json.load(f)
                except json.JSONDecodeError:
                    # Wenn das fehlschlägt, setze den Dateizeiger zurück und lese zeilenweise
                    f.seek(0)
                    chat_data_list = [json.loads(line) for line in f if line.strip()]

            print(f"Chat-Log '{chat_log_path}' enthält {len(chat_data_list)} Einträge.")

            for entry_index, entry in enumerate(chat_data_list):
                text = entry.get('message')
                # Zeitstempel ist oft 'time_in_seconds' oder relativ zu 'video_offset_seconds'
                # bei chat-downloader v1 (pip install chat-downloader)
                # ist es meist 'time_in_seconds' als Offset vom Videoanfang
                timestamp_sec = entry.get('time_in_seconds')

                if timestamp_sec is None and 'time_text' in entry:  # Fallback falls 'time_in_seconds' nicht da ist
                    # Versuche 'time_text' (z.B. "0:00:15") zu parsen, falls 'time_in_seconds' fehlt
                    try:
                        h, m, s = map(int, entry['time_text'].split(':'))
                        timestamp_sec = h * 3600 + m * 60 + s
                    except ValueError:
                        # print(f"Konnte time_text nicht parsen: {entry.get('time_text')}")
                        pass  # Ignoriere Nachrichten ohne klaren Zeitstempel

                if text and timestamp_sec is not None:
                    messages.append({'time_offset_seconds': float(timestamp_sec), 'text': str(text)})
                # else:
                #     if entry_index < 10 : # Nur die ersten paar Fehler loggen, um Konsole nicht zu fluten
                #          print(f"Nachricht ohne Text oder Zeitstempel in Log übersprungen: {entry}")

        except FileNotFoundError:
            print(f"FEHLER: Chat-Log-Datei nicht gefunden: {chat_log_path}")
        except Exception as e:
            print(f"FEHLER beim Parsen der Chat-Log-Datei {chat_log_path}: {e}")
            import traceback
            traceback.print_exc()
        return messages

    def process_messages_from_log(self, parsed_messages, video_duration_seconds):
        """
        Verarbeitet die geparsten Nachrichten und füllt das self.stream_features DataFrame.
        video_duration_seconds: Gesamtdauer des Videos in Sekunden.
        Die Zeitfenster (start_time, end_time) im DataFrame werden als Sekunden-Offsets vom Videoanfang gespeichert.
        """
        if not parsed_messages:
            print(f"[{self.stream_id_str}] Keine Chat-Nachrichten zum Verarbeiten gefunden.")
            # Erstelle ein leeres DataFrame mit den erwarteten Spalten, falls keine Nachrichten vorhanden sind
            self.stream_features = pd.DataFrame(columns=[
                'start_time_offset', 'end_time_offset',
                'message_counts', 'positive_message_count', 'negative_message_count'
            ]).astype({
                'start_time_offset': 'float64', 'end_time_offset': 'float64',
                'message_counts': 'int64', 'positive_message_count': 'int64', 'negative_message_count': 'int64'
            })
        else:
            # Stelle sicher, dass das DataFrame die korrekten Spalten und Typen hat
            self.stream_features = pd.DataFrame(columns=[
                'start_time_offset', 'end_time_offset',
                'message_counts', 'positive_message_count', 'negative_message_count'
            ]).astype({
                'start_time_offset': 'float64', 'end_time_offset': 'float64',
                'message_counts': 'int64', 'positive_message_count': 'int64', 'negative_message_count': 'int64'
            })

            # Erstelle Zeitfenster für die gesamte Videodauer
            num_intervals = int(video_duration_seconds / self.seconds_interval) + 1
            intervals_data = []
            for i in range(num_intervals):
                interval_start_offset = float(i * self.seconds_interval)
                interval_end_offset = float((i + 1) * self.seconds_interval)
                intervals_data.append({
                    'start_time_offset': interval_start_offset,
                    'end_time_offset': interval_end_offset,
                    'message_counts': 0,
                    'positive_message_count': 0,
                    'negative_message_count': 0
                })
            if intervals_data:  # Nur wenn Intervalle erstellt wurden (video_duration > 0)
                self.stream_features = pd.concat([self.stream_features, pd.DataFrame(intervals_data)],
                                                 ignore_index=True)
            else:  # Fallback, falls video_duration 0 ist, aber Nachrichten da sind (unwahrscheinlich)
                print(
                    f"WARNUNG [{self.stream_id_str}]: Videodauer ist 0, aber es gibt Chat-Nachrichten. Chat-Analyse könnte unvollständig sein.")

        # Verarbeite jede Nachricht
        for msg_info in parsed_messages:
            message_offset_seconds = msg_info['time_offset_seconds']
            message_text = msg_info['text']

            # Finde das richtige Zeitfenster im DataFrame
            try:
                interval_indices = self.stream_features[
                    (self.stream_features['start_time_offset'] <= message_offset_seconds) &
                    (self.stream_features['end_time_offset'] > message_offset_seconds)
                    ].index

                if not interval_indices.empty:
                    idx = interval_indices[0]
                    sentiment_list = self.classifier(message_text)
                    if sentiment_list:
                        sentiment = sentiment_list[0]
                        self.stream_features.loc[idx, 'message_counts'] += 1
                        if sentiment['label'] == 'POSITIVE':
                            self.stream_features.loc[idx, 'positive_message_count'] += 1
                        elif sentiment['label'] == 'NEGATIVE':  # Sicherstellen, dass es auch andere Labels gibt
                            self.stream_features.loc[idx, 'negative_message_count'] += 1
                # else:
                # print(f"Debug: Nachricht bei {message_offset_seconds}s nicht in Intervallen gefunden. Text: {message_text[:30]}")
                # print(f"Debug: Intervalle: \n{self.stream_features[['start_time_offset', 'end_time_offset']].head()}")

            except Exception as e:
                print(
                    f"FEHLER [{self.stream_id_str}] beim Zuordnen der Nachricht zum Zeitintervall: {message_text[:30]} bei {message_offset_seconds}s. Fehler: {e}")

        # Speichere das Ergebnis im MEDIA_ROOT Unterordner des Streams
        # z.B. media/uploads/user_id/stream_id/stream_id_chat_features.csv
        # Dafür brauchen wir user_id und stream_id
        # Da wir nur stream_id_str haben, speichern wir es erstmal allgemeiner
        # Der Pfad sollte identisch sein mit dem Ladepfad in generate_highlights_view

        output_dir = os.path.join(settings.MEDIA_ROOT, 'chat_analysis_results')
        try:
            os.makedirs(output_dir, exist_ok=True)
            output_csv_path = os.path.join(output_dir, f"{self.stream_id_str}_chat_features.csv")
            self.stream_features.to_csv(output_csv_path, index=False)
            print(f"INFO [{self.stream_id_str}]: Chat-Features gespeichert unter: {output_csv_path}")
        except Exception as e_save:
            print(f"FEHLER [{self.stream_id_str}] beim Speichern der Chat-Feature-CSV: {e_save}")