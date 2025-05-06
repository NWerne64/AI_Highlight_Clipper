# AI_Highlight_Clipper/scripts/background_recorder.py

import datetime
import enum
import logging
import os
import subprocess
import sys
import time
import requests
import argparse
import signal
import threading
import traceback


class TwitchResponseStatus(enum.Enum):
    ONLINE = 0
    OFFLINE = 1
    NOT_FOUND = 2
    UNAUTHORIZED = 3
    ERROR = 4


class BackgroundTwitchRecorder:
    def __init__(self, username, quality, stream_uid, output_path, client_id, client_secret, ffmpeg_path="ffmpeg",
                 disable_ffmpeg=False, refresh=15):
        self.username = username
        self.quality = quality
        self.stream_uid = stream_uid
        self.output_path = output_path
        self.client_id = client_id
        self.client_secret = client_secret
        self.ffmpeg_path = ffmpeg_path
        self.disable_ffmpeg = disable_ffmpeg
        self.refresh = max(15, refresh)
        self.token_url = f"https://id.twitch.tv/oauth2/token?client_id={self.client_id}&client_secret={self.client_secret}&grant_type=client_credentials"
        self.helix_url = "https://api.twitch.tv/helix/streams"
        self.access_token = None
        self.streamlink_process = None
        self.shutdown_event = threading.Event()

        script_dir = os.path.dirname(os.path.abspath(__file__))
        log_dir_path = os.path.join(script_dir, "recorder_logs")
        if not os.path.exists(log_dir_path):
            os.makedirs(log_dir_path, exist_ok=True)
        self.log_file = os.path.join(log_dir_path, f"recorder_{self.stream_uid}.log")

        log_format = f'%(asctime)s - UID:{self.stream_uid} - %(levelname)s - %(message)s'
        self.logger = logging.getLogger(f"BGRecorder_{self.stream_uid}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)
            handler.close()
        try:
            file_handler = logging.FileHandler(self.log_file, encoding='utf-8')
            file_handler.setLevel(logging.INFO)
            formatter = logging.Formatter(log_format)
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
        except Exception as log_e:
            print(f"FEHLER beim Erstellen des Log Handlers für {self.log_file}: {log_e}")
            self.logger = None

        if self.logger:
            self.logger.info(
                f"Recorder initialized for user '{username}', UID '{stream_uid}'. Output: {self.output_path}. Log: {self.log_file}")
        else:
            print(f"INIT (NO LOG) - UID:{self.stream_uid} - User:'{username}', Output: {self.output_path}")

        signal.signal(signal.SIGINT, self.graceful_shutdown_handler)
        signal.signal(signal.SIGTERM, self.graceful_shutdown_handler)

    def _log(self, message, level=logging.INFO):
        if self.logger:
            self.logger.log(level, message)
        else:
            print(f"LOG UID:{self.stream_uid} - {logging.getLevelName(level)} - {message}")

    def graceful_shutdown_handler(self, signum, frame):
        signal_name = signal.Signals(signum).name
        self._log(f"Signal {signal_name} ({signum}) empfangen. Starte sauberes Herunterfahren.", logging.WARNING)
        self.shutdown_event.set()

        if self.streamlink_process and self.streamlink_process.poll() is None:
            self._log("Sende SIGINT (Ctrl+C) an Streamlink-Prozess, um Aufnahme zu finalisieren...", logging.INFO)
            try:
                if os.name == 'nt':
                    self.streamlink_process.terminate()
                    self._log("SIGTERM an Streamlink gesendet (Windows).", logging.INFO)
                else:
                    self.streamlink_process.send_signal(signal.SIGINT)
                    self._log("SIGINT an Streamlink gesendet (Unix-like).", logging.INFO)
                time.sleep(1)
            except Exception as e:
                self._log(f"Fehler beim Senden des Beendigungssignals an Streamlink: {e}", logging.ERROR)
                self.streamlink_process.kill()
        else:
            self._log("Kein laufender Streamlink-Prozess gefunden oder bereits beendet beim Signalempfang.",
                      logging.INFO)

    def fetch_access_token(self):
        try:
            self._log("Rufe Access Token ab...")
            token_response = requests.post(self.token_url, timeout=15)
            token_response.raise_for_status()
            token = token_response.json()
            self.access_token = token["access_token"]
            self._log("Access Token erfolgreich abgerufen.")
            return True
        except requests.exceptions.RequestException as e:
            self._log(f"Fehler beim Abrufen des Access Tokens: {e}", logging.ERROR)
            return False

    def check_user(self):
        if not self.access_token:
            if not self.fetch_access_token():
                return TwitchResponseStatus.ERROR, None

        status, info = TwitchResponseStatus.ERROR, None
        try:
            headers = {"Client-ID": self.client_id, "Authorization": "Bearer " + self.access_token}
            params = {"user_login": self.username}
            self._log(f"Prüfe Twitch API für Benutzer: {self.username}")
            r = requests.get(self.helix_url, headers=headers, params=params, timeout=15)
            r.raise_for_status()
            info = r.json()
            if info is None or not info.get("data"):
                status = TwitchResponseStatus.OFFLINE
                self._log(f"Benutzer {self.username} ist OFFLINE.")
            else:
                status = TwitchResponseStatus.ONLINE
                self._log(f"Benutzer {self.username} ist ONLINE: {info['data'][0]['title']}")
        except requests.exceptions.RequestException as e:
            self._log(f"Fehler beim Prüfen des Benutzerstatus: {e}", logging.ERROR)
            if e.response is not None:
                self._log(f"Antwortstatus: {e.response.status_code}, Text: {e.response.text}", logging.ERROR)
                if e.response.status_code == 401:
                    status = TwitchResponseStatus.UNAUTHORIZED
                    self.access_token = None
                elif e.response.status_code == 404:
                    status = TwitchResponseStatus.NOT_FOUND
                else:
                    status = TwitchResponseStatus.ERROR
            else:
                status = TwitchResponseStatus.ERROR
        except Exception as ex:
            self._log(f"Unerwarteter Fehler bei der Benutzerprüfung: {ex}\n{traceback.format_exc()}", logging.ERROR)
            status = TwitchResponseStatus.ERROR
        return status, info

    def record_stream(self):
        output_dir = os.path.dirname(self.output_path)
        if not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir, exist_ok=True)
                self._log(f"Ausgabeverzeichnis erstellt: {output_dir}")
            except OSError as e:
                self._log(f"Konnte Ausgabeverzeichnis nicht erstellen {output_dir}: {e}", logging.CRITICAL)
                return False

        streamlink_cmd = [
            "streamlink",
            "--twitch-disable-ads",
            f"twitch.tv/{self.username}",
            self.quality,
            "-o", self.output_path
        ]
        self._log(f"Starte Streamlink: {' '.join(streamlink_cmd)}")

        try:
            self.streamlink_process = subprocess.Popen(streamlink_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                       text=True, encoding='utf-8')
            self._log(f"Streamlink gestartet mit PID: {self.streamlink_process.pid}")

            while self.streamlink_process.poll() is None:
                if self.shutdown_event.is_set():
                    self._log("Shutdown-Event während der Aufnahme erkannt. Beende Streamlink...", logging.INFO)
                    break
                time.sleep(0.5)

            timeout_seconds = 45
            self._log(f"Warte auf Beendigung des Streamlink-Prozesses (max. {timeout_seconds}s)...")

            try:
                stdout, stderr = self.streamlink_process.communicate(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                self._log(f"Streamlink Timeout ({timeout_seconds}s) nach Beendigungssignal! Prozess wird gekillt.",
                          logging.WARNING)
                self.streamlink_process.kill()
                stdout, stderr = self.streamlink_process.communicate()

            return_code = self.streamlink_process.returncode
            if stdout: self._log(f"Streamlink stdout:\n{stdout.strip()}", logging.DEBUG)

            if self.shutdown_event.is_set():
                self._log(f"Streamlink beendet (RC: {return_code}) nach externem Shutdown-Signal.", logging.INFO)
                if os.path.exists(self.output_path) and os.path.getsize(self.output_path) > 0:
                    self._log(
                        "Aufnahmedatei existiert und ist nicht leer. Aufnahme gilt als 'erfolgreich' für die Nachbearbeitung.")
                    return True
                else:
                    self._log("Aufnahmedatei nach Shutdown nicht gefunden oder leer. Aufnahme fehlgeschlagen.",
                              logging.WARNING)
                    if stderr: self._log(f"Streamlink stderr (nach Shutdown):\n{stderr.strip()}", logging.WARNING)
                    return False

            if return_code == 0:
                self._log("Streamlink Aufnahme erfolgreich beendet (normaler Exit).")
                return True
            else:
                self._log(f"Streamlink Aufnahme fehlgeschlagen! Rückgabecode: {return_code}", logging.ERROR)
                if stderr: self._log(f"Streamlink stderr:\n{stderr.strip()}", logging.ERROR)
                if os.path.exists(self.output_path):
                    try:
                        os.remove(self.output_path)
                        self._log("Unvollständige Aufnahmedatei nach Streamlink-Fehler gelöscht.")
                    except OSError as e_rm:
                        self._log(f"Konnte unvollständige Datei nicht löschen: {self.output_path}: {e_rm}",
                                  logging.WARNING)
                return False

        except FileNotFoundError:
            self._log("FEHLER: 'streamlink' Kommando nicht gefunden. Ist Streamlink installiert und im PATH?",
                      logging.CRITICAL)
            return False
        except Exception as e:
            self._log(f"Unerwarteter Fehler während der Streamlink-Ausführung: {e}\n{traceback.format_exc()}",
                      logging.CRITICAL)
            return False

    def process_recorded_file(self):
        if not os.path.exists(self.output_path) or os.path.getsize(self.output_path) == 0:
            self._log(f"Aufnahmedatei {self.output_path} nicht vorhanden oder leer. Überspringe FFmpeg-Verarbeitung.",
                      logging.WARNING)
            return False

        if self.disable_ffmpeg:
            self._log("FFmpeg-Verarbeitung ist deaktiviert.")
            return True

        temp_fixed_path = self.output_path + ".fixed.mp4"
        ffmpeg_cmd = [self.ffmpeg_path, "-nostdin", "-i", self.output_path, "-c", "copy", "-map", "0", "-loglevel",
                      "error", temp_fixed_path, "-y"]
        self._log(f"Starte FFmpeg zur Überprüfung/Reparatur: {' '.join(ffmpeg_cmd)}")

        try:
            ffmpeg_process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                              encoding='utf-8')
            timeout_ffmpeg = 300
            self._log(f"Warte auf FFmpeg (max. {timeout_ffmpeg}s)...")
            stdout, stderr = ffmpeg_process.communicate(timeout=timeout_ffmpeg)

            return_code = ffmpeg_process.returncode

            if return_code == 0:
                os.replace(temp_fixed_path, self.output_path)
                self._log(f"FFmpeg-Verarbeitung erfolgreich abgeschlossen für {self.output_path}")
                return True
            else:
                self._log(f"FFmpeg-Verarbeitung fehlgeschlagen! Rückgabecode: {return_code}", logging.ERROR)
                if stderr: self._log(f"FFmpeg stderr:\n{stderr.strip()}", logging.ERROR)
                if stdout: self._log(f"FFmpeg stdout:\n{stdout.strip()}", logging.DEBUG)
                if os.path.exists(temp_fixed_path):
                    try:
                        os.remove(temp_fixed_path)
                    except OSError as e_rm_ffmpeg:
                        self._log(f"Konnte temporäre FFmpeg-Datei nicht löschen: {e_rm_ffmpeg}", logging.WARNING)
                return False

        except FileNotFoundError:
            self._log(f"FEHLER: '{self.ffmpeg_path}' (FFmpeg) Kommando nicht gefunden. Verarbeitung nicht möglich.",
                      logging.CRITICAL)
            return False
        except subprocess.TimeoutExpired:
            self._log(f"FFmpeg-Verarbeitung Timeout ({timeout_ffmpeg}s). Prozess wird gekillt.", logging.ERROR)
            if 'ffmpeg_process' in locals() and ffmpeg_process.poll() is None:
                ffmpeg_process.kill()
                ffmpeg_process.communicate()
            if os.path.exists(temp_fixed_path):
                try:
                    os.remove(temp_fixed_path)
                except OSError as e_rm_timeout:
                    self._log(f"Konnte temp. FFmpeg-Datei nach Timeout nicht löschen: {e_rm_timeout}", logging.WARNING)
            return False
        except Exception as e:
            self._log(f"Unerwarteter Fehler während der FFmpeg-Verarbeitung: {e}\n{traceback.format_exc()}",
                      logging.CRITICAL)
            if os.path.exists(temp_fixed_path):
                try:
                    os.remove(temp_fixed_path)
                except OSError as e_rm_unexp:
                    self._log(f"Konnte temp. FFmpeg-Datei nach unerw. Fehler nicht löschen: {e_rm_unexp}",
                              logging.WARNING)
            return False

    def run_check_and_record(self):
        self._log(f"--- Starte Aufnahme-Task für {self.username} (UID: {self.stream_uid}) ---")

        status, _ = self.check_user()
        final_task_success = False

        if self.shutdown_event.is_set():
            self._log("Shutdown-Signal bereits vor Beginn der Benutzerprüfung/Aufnahme empfangen. Breche ab.",
                      logging.WARNING)
            return False

        if status == TwitchResponseStatus.ONLINE:
            self._log(f"Benutzer {self.username} ist online. Starte Aufnahmeversuch...")
            if self.record_stream():
                if self.process_recorded_file():
                    self._log(f"--- Aufnahme und FFmpeg-Verarbeitung für {self.output_path} ERFOLGREICH ---")
                    final_task_success = True
                else:
                    self._log(
                        f"--- Aufnahme für {self.output_path} OK, aber FFmpeg-Verarbeitung FEHLGESCHLAGEN. Rohdatei könnte vorhanden sein. ---",
                        logging.ERROR)
                    final_task_success = False
            else:
                self._log(
                    f"--- Aufnahme für UID: {self.stream_uid} FEHLGESCHLAGEN (record_stream gab False zurück). ---",
                    logging.ERROR)
        elif status == TwitchResponseStatus.OFFLINE:
            self._log(f"--- Benutzer {self.username} ist offline. Task beendet. ---")
        elif self.shutdown_event.is_set():
            self._log("Shutdown-Signal während der Benutzerprüfung empfangen. Breche ab.", logging.WARNING)
        else:
            self._log(f"--- Prüfung für Benutzer {self.username} fehlgeschlagen (Status: {status}). Task beendet. ---",
                      logging.ERROR)

        if self.shutdown_event.is_set():
            self._log(
                f"--- Aufnahme-Task für UID: {self.stream_uid} wurde durch Shutdown-Signal beendet oder unterbrochen. ---",
                logging.WARNING)

        return final_task_success


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Record a Twitch stream if online, with graceful shutdown.")
    parser.add_argument("--username", required=True, help="Twitch username.")
    parser.add_argument("--quality", default="480p", help="Stream quality (z.B. 720p, best, worst).")
    parser.add_argument("--uid", required=True, help="Unique Stream ID from Django.")
    parser.add_argument("--output-path", required=True, help="Full path for the recording (inkl. Dateiname .mp4).")
    parser.add_argument("--client-id", required=True, help="Twitch Client ID.")
    parser.add_argument("--client-secret", required=True, help="Twitch Client Secret.")
    parser.add_argument("--disable-ffmpeg", action="store_true", help="Disable ffmpeg post-processing.")
    args = parser.parse_args()

    recorder = BackgroundTwitchRecorder(
        username=args.username, quality=args.quality, stream_uid=args.uid,
        output_path=args.output_path, client_id=args.client_id,
        client_secret=args.client_secret, disable_ffmpeg=args.disable_ffmpeg
    )

    exit_code = 1
    try:
        if recorder.run_check_and_record():
            exit_code = 0
    except Exception as e_main:
        if recorder.logger:
            recorder.logger.critical(
                f"Kritischer, unerwarteter Fehler im Hauptteil des Recorders: {e_main}\n{traceback.format_exc()}",
                exc_info=False)
        else:
            print(f"KRITISCHER FEHLER (UID {args.uid}): {e_main}\n{traceback.format_exc()}")
    finally:
        if recorder.logger:
            recorder.logger.info(f"Recorder-Skript für UID {args.uid} wird beendet mit Exit-Code: {exit_code}.")
            logging.shutdown()
        else:
            print(f"Recorder-Skript für UID {args.uid} beendet mit Exit-Code: {exit_code}.")

    sys.exit(exit_code)