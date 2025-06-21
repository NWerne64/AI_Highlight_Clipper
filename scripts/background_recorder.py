# Vereinfachte Version für scripts/background_recorder.py

import sys
import os
import subprocess
import argparse
import time
import django

# Django-Setup
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'highlights.settings')
django.setup()

from webapp.viewer.models import Stream


def main(stream_id, twitch_channel, output_dir):
    """
    Diese Funktion nimmt nur noch den Stream auf und setzt am Ende den Status.
    Die Nachbearbeitung erfolgt in einem separaten Schritt in Django.
    """
    stream_obj = Stream.objects.get(id=stream_id)

    # Dateiname basierend auf der Stream-ID
    filename = f"{stream_id}.mp4"
    output_path = os.path.join(output_dir, filename)

    # Streamlink-Befehl
    streamlink_command = [
        'streamlink',
        '--twitch-disable-ads',
        f'twitch.tv/{twitch_channel}',
        'best',
        '-o',
        output_path
    ]

    print(f"Starting streamlink recording for {twitch_channel}...")
    streamlink_process = subprocess.Popen(streamlink_command)

    stream_obj.recorder_pid = os.getpid()
    stream_obj.save(update_fields=['recorder_pid'])

    print(f"Recorder script running with PID: {os.getpid()}")

    stop_file_path = os.path.join(output_dir, 'stop_recording.flag')

    try:
        # Schleife, die läuft, solange die Aufnahme aktiv ist
        while streamlink_process.poll() is None:
            if os.path.exists(stop_file_path):
                print("Stop signal file detected. Terminating streamlink...")
                streamlink_process.terminate()
                streamlink_process.wait()
                os.remove(stop_file_path)
                break
            time.sleep(2)
    finally:
        print("--- Aufnahme beendet. Setze finalen Status. ---")

        # Prüfen, ob eine gültige Datei erstellt wurde
        video_exists = os.path.exists(output_path) and os.path.getsize(output_path) > 1024

        # Setze den Status auf PENDING, um die manuelle Nachbearbeitung zu signalisieren
        final_status = 'PENDING' if video_exists else 'ERROR_NO_FILE'

        Stream.objects.filter(id=stream_id).update(
            recorder_pid=None,
            analysis_status=final_status
        )
        print(f"--- Finaler Status gesetzt auf: {final_status} ---")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Simplified Twitch Stream Recorder")
    parser.add_argument("--stream_id", required=True)
    parser.add_argument("--twitch_channel", required=True)
    parser.add_argument("--output_dir", required=True)

    args = parser.parse_args()
    main(args.stream_id, args.twitch_channel, args.output_dir)