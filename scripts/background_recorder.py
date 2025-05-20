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
import shutil  # Wichtig für shutil.move


class TwitchResponseStatus(enum.Enum):
    ONLINE = 0
    OFFLINE = 1
    NOT_FOUND = 2
    UNAUTHORIZED = 3
    ERROR = 4


class BackgroundTwitchRecorder:
    def __init__(self, username, quality, stream_uid, output_path, client_id, client_secret,
                 ffmpeg_path="ffmpeg", disable_ffmpeg=False, refresh=15, streamlink_path="streamlink"):
        self.username = username
        self.quality = quality
        self.stream_uid = stream_uid
        self.output_path = output_path
        self.client_id = client_id
        self.client_secret = client_secret
        self.ffmpeg_path = ffmpeg_path
        self.streamlink_path = streamlink_path
        self.disable_ffmpeg = disable_ffmpeg
        self.refresh = max(15, refresh)
        self.token_url = f"https://id.twitch.tv/oauth2/token?client_id={self.client_id}&client_secret={self.client_secret}&grant_type=client_credentials"
        self.helix_url = "https://api.twitch.tv/helix/streams"
        self.access_token = None
        self.streamlink_process = None
        self.shutdown_event = threading.Event()

        script_dir = os.path.dirname(os.path.abspath(__file__))
        log_dir_path = os.path.join(script_dir, "recorder_logs")
        os.makedirs(log_dir_path, exist_ok=True)
        self.log_file = os.path.join(log_dir_path, f"recorder_{self.stream_uid}.log")

        log_format = f'%(asctime)s - UID:{self.stream_uid} - %(levelname)s - %(message)s'
        self.logger = logging.getLogger(f"BGRecorder_{self.stream_uid}")
        if not self.logger.hasHandlers():
            self.logger.setLevel(logging.INFO)
            self.logger.propagate = False
            try:
                file_handler = logging.FileHandler(self.log_file, encoding='utf-8')
                file_handler.setLevel(logging.INFO)
                formatter = logging.Formatter(log_format)
                file_handler.setFormatter(formatter)
                self.logger.addHandler(file_handler)
            except Exception as log_e:
                print(f"FEHLER beim Erstellen des Log Handlers für {self.log_file}: {log_e}")
                self.logger = None

        self._log(
            f"Recorder initialized. User: '{username}', UID: '{stream_uid}', Output: {self.output_path}, Log: {self.log_file}")

        signal.signal(signal.SIGINT, self.graceful_shutdown_handler)
        signal.signal(signal.SIGTERM, self.graceful_shutdown_handler)

    def _log(self, message, level=logging.INFO):
        if self.logger:
            self.logger.log(level, message)
        else:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
            print(f"{timestamp} - UID:{self.stream_uid} - {logging.getLevelName(level)} - {message}")

    def graceful_shutdown_handler(self, signum, frame):
        signal_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else f"Signal {signum}"
        self._log(f"Signal {signal_name} empfangen. Starte sauberes Herunterfahren.", logging.WARNING)
        self.shutdown_event.set()

        if self.streamlink_process and self.streamlink_process.poll() is None:
            self._log("Sende Signal an Streamlink-Prozess...", logging.INFO)
            try:
                if os.name == 'nt':
                    self.streamlink_process.terminate()  # SIGTERM auf Windows
                    self._log("SIGTERM an Streamlink gesendet (Windows).", logging.INFO)
                else:
                    self.streamlink_process.send_signal(signal.SIGINT)  # SIGINT auf Unix
                    self._log("SIGINT an Streamlink gesendet (Unix-like).", logging.INFO)

                # Warte auf Streamlink mit Timeout
                self.streamlink_process.wait(timeout=30)  # 30 Sekunden
                self._log(f"Streamlink-Prozess beendet nach Signal mit RC: {self.streamlink_process.returncode}.",
                          logging.INFO)
            except subprocess.TimeoutExpired:
                self._log("Streamlink-Prozess nicht innerhalb von 30s nach Signal beendet. Erzwinge Kill.",
                          logging.WARNING)
                self.streamlink_process.kill()
                self.streamlink_process.wait()
            except Exception as e:
                self._log(f"Fehler beim Beenden von Streamlink: {e}", logging.ERROR)
                if self.streamlink_process.poll() is None:
                    self.streamlink_process.kill()
                    self.streamlink_process.wait()
        else:
            self._log("Kein laufender Streamlink-Prozess oder bereits beendet.", logging.INFO)

    def fetch_access_token(self):
        try:
            self._log("Rufe Access Token ab...")
            response = requests.post(self.token_url, timeout=15)
            response.raise_for_status()
            token_data = response.json()
            self.access_token = token_data.get("access_token")
            if self.access_token:
                self._log("Access Token erfolgreich.")
                return True
            self._log(f"Fehler: 'access_token' nicht in API Antwort. Antwort: {token_data}", logging.ERROR)
            return False
        except Exception as e:
            self._log(f"Fehler beim Abrufen des Access Tokens: {e}", logging.ERROR)
            return False

    def check_user(self):
        if not self.access_token and not self.fetch_access_token():
            return TwitchResponseStatus.ERROR, None
        try:
            headers = {"Client-ID": self.client_id, "Authorization": "Bearer " + self.access_token}
            params = {"user_login": self.username}
            r = requests.get(self.helix_url, headers=headers, params=params, timeout=15)
            r.raise_for_status()
            info = r.json()
            if info.get("data"):
                self._log(f"Benutzer {self.username} ist ONLINE: {info['data'][0].get('title', 'N/A')}")
                return TwitchResponseStatus.ONLINE, info
            self._log(f"Benutzer {self.username} ist OFFLINE.")
            return TwitchResponseStatus.OFFLINE, info
        except requests.exceptions.HTTPError as http_err:
            # ... (Fehlerbehandlung wie zuvor)
            if http_err.response.status_code == 401: self.access_token = None; return TwitchResponseStatus.UNAUTHORIZED, None
            if http_err.response.status_code == 404: return TwitchResponseStatus.NOT_FOUND, None
            self._log(f"HTTP Fehler bei Benutzerprüfung: {http_err}", logging.ERROR)
            return TwitchResponseStatus.ERROR, None
        except Exception as ex:
            self._log(f"Unerwarteter Fehler bei Benutzerprüfung: {ex}", logging.ERROR)
            return TwitchResponseStatus.ERROR, None

    def record_stream(self):
        output_dir = os.path.dirname(self.output_path)
        os.makedirs(output_dir, exist_ok=True)

        streamlink_cmd = [
            self.streamlink_path, "--twitch-disable-ads",
            f"twitch.tv/{self.username}", self.quality,
            "-o", self.output_path,
            "--ffmpeg-ffmpeg", self.ffmpeg_path, "--force"
        ]
        self._log(f"Starte Streamlink: {' '.join(streamlink_cmd)}")
        try:
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            self.streamlink_process = subprocess.Popen(streamlink_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                       text=True, encoding='utf-8', creationflags=creation_flags)
            self._log(f"Streamlink gestartet mit PID: {self.streamlink_process.pid}")

            while self.streamlink_process.poll() is None:
                if self.shutdown_event.is_set():
                    self._log("Shutdown während Aufnahme. Beende Streamlink...", logging.INFO)
                    self.graceful_shutdown_handler(signal.SIGTERM if os.name != 'nt' else signal.SIGINT, None)
                    break  # Aus der Warteschleife
                time.sleep(0.5)

            # Finale Kommunikation nach Beendigung (oder Timeout von graceful_shutdown_handler)
            stdout, stderr = "", ""
            if self.streamlink_process:  # Nur wenn Prozessobjekt existiert
                try:
                    stdout, stderr = self.streamlink_process.communicate(timeout=15)  # Kurzes Timeout für Reste
                except subprocess.TimeoutExpired:
                    self._log("Streamlink communicate() timed out after process end/kill.", logging.WARNING)
                    if self.streamlink_process.poll() is None: self.streamlink_process.kill()  # Sicherstellen, dass er tot ist
                return_code = self.streamlink_process.returncode
            else:  # Sollte nicht passieren, wenn graceful_shutdown_handler richtig funktioniert
                return_code = -1  # Unbekannter Fehler
                self._log("Streamlink-Prozessobjekt war None nach der Schleife.", logging.ERROR)

            if stdout: self._log(f"Streamlink stdout:\n{stdout.strip()}", logging.DEBUG)

            if return_code == 0:
                self._log("Streamlink Aufnahme erfolgreich beendet.")
            elif self.shutdown_event.is_set():
                self._log(f"Streamlink durch Shutdown beendet (RC: {return_code}). Datei könnte unvollständig sein.",
                          logging.WARNING)
                if stderr: self._log(f"Streamlink stderr (Shutdown):\n{stderr.strip()}", logging.WARNING)
            else:
                self._log(f"Streamlink Aufnahme fehlgeschlagen! RC: {return_code}", logging.ERROR)
                if stderr: self._log(f"Streamlink stderr:\n{stderr.strip()}", logging.ERROR)
                if os.path.exists(self.output_path): os.remove(self.output_path)
                return False  # Klare Fehlermeldung

            if os.path.exists(self.output_path) and os.path.getsize(self.output_path) > 1000:
                self._log("Aufnahmedatei existiert und ist >1KB.")
                return True  # Datei ist da, Optimierung kann versucht werden
            self._log("Aufnahmedatei nicht gefunden oder leer nach Streamlink.", logging.ERROR)
            return False
        except Exception as e:
            self._log(f"Unerwarteter Fehler während Streamlink-Ausführung: {e}", logging.CRITICAL)
            traceback.print_exc()
            if hasattr(self,
                       'streamlink_process') and self.streamlink_process and self.streamlink_process.poll() is None:
                self.streamlink_process.kill()
            return False

    def process_recorded_file(self):
        if self.disable_ffmpeg:
            self._log("FFmpeg-Verarbeitung ist deaktiviert.")
            return os.path.exists(self.output_path) and os.path.getsize(self.output_path) > 1000

        if not os.path.exists(self.output_path) or os.path.getsize(self.output_path) < 1000:
            self._log(f"Aufnahmedatei {self.output_path} für FFmpeg nicht geeignet oder nicht vorhanden.",
                      logging.WARNING)
            return False

        base, ext = os.path.splitext(self.output_path);
        ext = ext or ".mp4"
        # Ein etwas anderer temporärer Name, um sicherzustellen, dass wir nicht versehentlich das Original überschreiben,
        # bevor wir sicher sind, dass das Repacking erfolgreich war.
        temp_optimized_path = base + ".optimized_temp" + ext

        ffmpeg_cmd = [
            self.ffmpeg_path, "-nostdin",
            "-i", self.output_path,
            "-c", "copy", "-map", "0", "-movflags", "+faststart",
            "-loglevel", "error", temp_optimized_path, "-y"
        ]
        self._log(f"Starte FFmpeg Optimierung: {' '.join(ffmpeg_cmd)}")
        ffmpeg_process = None

        try:
            # Stelle sicher, dass alle Handles zum self.output_path von Streamlink geschlossen sind.
            # Streamlink sollte die Datei nach Beendigung schließen.
            # Wenn ffmpeg die Datei nicht öffnen kann, ist das ein Hinweis darauf, dass sie noch gesperrt ist.

            ffmpeg_process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                              encoding='utf-8')
            # Warte auf den FFmpeg-Prozess. Timeout sollte großzügig sein für große Dateien.
            timeout_ffmpeg_config_key = 'FFMPEG_REPACK_TIMEOUT_RECORDER'  # Eigener Key für Recorder
            default_timeout = 1800  # 30 Minuten Default
            # Versuche, Timeout aus Django Settings zu laden, falls im Django Kontext.
            # Da dieses Skript extern läuft, ist der direkte Zugriff auf Django settings nicht immer gegeben.
            # Die Übergabe als Argument wäre robuster, aber für jetzt versuchen wir es so, falls es als Modul importiert wird.
            timeout_ffmpeg = default_timeout
            try:
                from django.conf import settings as django_settings
                timeout_ffmpeg = getattr(django_settings, timeout_ffmpeg_config_key, default_timeout)
            except ImportError:
                pass  # Django settings nicht verfügbar, verwende Default

            stdout, stderr = ffmpeg_process.communicate(timeout=timeout_ffmpeg)
            return_code = ffmpeg_process.returncode

            if return_code == 0:
                self._log(f"FFmpeg Optimierung erfolgreich abgeschlossen. Temporäre Datei: {temp_optimized_path}")

                # WICHTIG: Stelle sicher, dass der ffmpeg_process beendet ist und seine Handles freigegeben hat.
                # communicate() wartet bereits darauf.

                # Kurze Pause, um dem Dateisystem Zeit zu geben, Handles vollständig freizugeben
                time.sleep(2)  # Erhöhe auf 2 Sekunden, um sicherzugehen

                try:
                    # Prüfe, ob die temporäre Datei wirklich existiert und eine plausible Größe hat
                    if not os.path.exists(temp_optimized_path) or os.path.getsize(temp_optimized_path) < 1000:
                        self._log(
                            f"FEHLER: Optimierte Datei {temp_optimized_path} nicht gefunden oder zu klein nach FFmpeg-Erfolg.",
                            logging.ERROR)
                        # Originaldatei behalten, da Optimierung fehlschlug
                        return os.path.exists(self.output_path) and os.path.getsize(self.output_path) > 1000

                    # Jetzt die Dateioperationen
                    shutil.move(temp_optimized_path, self.output_path)  # shutil.move überschreibt das Ziel
                    self._log(f"Original-Aufnahme '{self.output_path}' erfolgreich durch optimierte Version ersetzt.")
                    return True
                except Exception as e_move:
                    self._log(
                        f"FEHLER beim Ersetzen der Originaldatei ('{self.output_path}') durch die optimierte Datei ('{temp_optimized_path}'): {e_move}",
                        logging.CRITICAL)
                    traceback.print_exc()
                    # Wenn das Verschieben fehlschlägt, versuchen wir, die temporäre Datei zu löschen.
                    # Die Originaldatei (nicht optimiert) bleibt bestehen.
                    if os.path.exists(temp_optimized_path):
                        try:
                            os.remove(temp_optimized_path)
                        except Exception as e_rm_temp:
                            self._log(
                                f"Konnte temp. optimierte Datei {temp_optimized_path} nach Fehler nicht löschen: {e_rm_temp}",
                                logging.WARNING)
                    self._log(
                        "WARNUNG: FFmpeg-Optimierung war erfolgreich, aber die optimierte Datei konnte die Originaldatei nicht ersetzen. Original wird verwendet.",
                        logging.WARNING)
                    return os.path.exists(self.output_path) and os.path.getsize(
                        self.output_path) > 1000  # Erfolg, wenn Original noch da
            else:
                self._log(f"FFmpeg-Optimierung fehlgeschlagen! Rückgabecode: {return_code}", logging.ERROR)
                if stderr: self._log(f"FFmpeg stderr:\n{stderr.strip()}", logging.ERROR)
                if stdout: self._log(f"FFmpeg stdout (bei Fehler):\n{stdout.strip()}", logging.DEBUG)
                if os.path.exists(temp_optimized_path):
                    try:
                        os.remove(temp_optimized_path)
                    except Exception as e_rm_temp_fail:
                        self._log(
                            f"Konnte temp. optimierte Datei '{temp_optimized_path}' nach FFmpeg-Fehler nicht löschen: {e_rm_temp_fail}",
                            logging.WARNING)
                self._log("WARNUNG: FFmpeg-Optimierung fehlgeschlagen. Originalaufnahme wird verwendet.",
                          logging.WARNING)
                return os.path.exists(self.output_path) and os.path.getsize(self.output_path) > 1000

        except subprocess.TimeoutExpired:
            self._log(f"FFmpeg-Optimierungsprozess Timeout ({timeout_ffmpeg}s).", logging.ERROR)
            if ffmpeg_process and ffmpeg_process.poll() is None:
                ffmpeg_process.kill();
                ffmpeg_process.communicate()
            if os.path.exists(temp_optimized_path):
                try:
                    os.remove(temp_optimized_path)
                except Exception as e_rm_timeout:
                    self._log(
                        f"Konnte temp. optimierte Datei '{temp_optimized_path}' nach Timeout nicht löschen: {e_rm_timeout}",
                        logging.WARNING)
            self._log("WARNUNG: FFmpeg-Optimierung Timeout. Originalaufnahme wird verwendet.", logging.WARNING)
            return os.path.exists(self.output_path) and os.path.getsize(self.output_path) > 1000
        except FileNotFoundError:
            self._log(f"FEHLER: '{self.ffmpeg_path}' (FFmpeg) wurde nicht gefunden. Optimierung übersprungen.",
                      logging.CRITICAL)
            return os.path.exists(self.output_path) and os.path.getsize(self.output_path) > 1000
        except Exception as e:
            self._log(f"Unerwarteter Fehler während der FFmpeg-Optimierung: {e}", logging.CRITICAL)
            traceback.print_exc()
            if os.path.exists(temp_optimized_path):
                try:
                    os.remove(temp_optimized_path)
                except Exception as e_rm_unexp:
                    self._log(
                        f"Konnte temp. optimierte Datei '{temp_optimized_path}' nach unerwartetem Fehler nicht löschen: {e_rm_unexp}",
                        logging.WARNING)
            self._log("WARNUNG: Unerwarteter Fehler bei FFmpeg-Optimierung. Originalaufnahme wird verwendet.",
                      logging.WARNING)
            return os.path.exists(self.output_path) and os.path.getsize(self.output_path) > 1000

    def run_check_and_record(self):
        self._log(f"--- Aufnahme-Task gestartet für {self.username} ---")
        final_task_success = False
        try:
            if self.shutdown_event.is_set(): return False
            status, _ = self.check_user()
            if self.shutdown_event.is_set(): return False

            if status == TwitchResponseStatus.ONLINE:
                if self.record_stream():  # Nimmt auf, gibt True zurück, wenn Datei existiert
                    # Wichtig: process_recorded_file wird nur aufgerufen, wenn record_stream erfolgreich war
                    # UND die Datei auch wirklich da ist (wird in record_stream geprüft)
                    if self.process_recorded_file():  # Optimiert oder verwendet Original
                        self._log(f"Aufnahme und Nachbearbeitung für {self.output_path} abgeschlossen.")
                        final_task_success = True
                    else:  # process_recorded_file gab False zurück (z.B. Fehler beim Verschieben der optimierten Datei)
                        self._log(
                            f"Aufnahme erstellt, aber kritischer Fehler bei Nachbearbeitung von {self.output_path}.",
                            logging.ERROR)
                else:  # record_stream gab False zurück
                    self._log("Aufnahme fehlgeschlagen (record_stream).", logging.ERROR)
            elif status == TwitchResponseStatus.OFFLINE:
                self._log("Benutzer offline. Task beendet.")
            else:
                self._log(f"Benutzerprüfung fehlgeschlagen (Status: {status}). Task beendet.", logging.ERROR)
        except Exception as e_run_main:
            self._log(f"KRITISCHER FEHLER in run_check_and_record: {e_run_main}", logging.CRITICAL)
            traceback.print_exc()
        finally:
            log_level = logging.WARNING if self.shutdown_event.is_set() else logging.INFO
            self._log(f"--- Aufnahme-Task beendet. Erfolg: {final_task_success} ---", level=log_level)
            if self.logger:
                for handler in self.logger.handlers[:]: handler.close(); self.logger.removeHandler(handler)
                logging.shutdown()
        return final_task_success


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Twitch Stream Recorder")
    parser.add_argument("--username", required=True)
    parser.add_argument("--quality", default="480p")
    parser.add_argument("--uid", required=True)
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-secret", required=True)
    parser.add_argument("--ffmpeg-path", default="ffmpeg")
    parser.add_argument("--streamlink-path", default="streamlink")
    parser.add_argument("--disable-ffmpeg", action="store_true")
    parser.add_argument("--refresh-interval", type=int, default=15)
    args = parser.parse_args()

    recorder = BackgroundTwitchRecorder(
        username=args.username, quality=args.quality, stream_uid=args.uid,
        output_path=args.output_path, client_id=args.client_id,
        client_secret=args.client_secret, ffmpeg_path=args.ffmpeg_path,
        disable_ffmpeg=args.disable_ffmpeg, refresh=args.refresh_interval,
        streamlink_path=args.streamlink_path
    )
    exit_code = 1
    try:
        if recorder.run_check_and_record(): exit_code = 0
    except Exception as e_main_script:
        print(f"Kritischer Fehler im Skript-Hauptteil (UID {args.uid}): {e_main_script}\n{traceback.format_exc()}")
    finally:
        print(f"Recorder-Skript (UID {args.uid}) beendet mit Exit-Code: {exit_code}.")
    sys.exit(exit_code)