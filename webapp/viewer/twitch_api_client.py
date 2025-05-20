# webapp/viewer/twitch_api_client.py

import requests
import logging
from django.conf import settings
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Globale Variable für den Access Token und dessen Ablaufzeit
APP_ACCESS_TOKEN = None
TOKEN_EXPIRATION_TIME = None
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
HELIX_URL_VIDEOS = "https://api.twitch.tv/helix/videos"
HELIX_URL_USERS = "https://api.twitch.tv/helix/users"


def _fetch_app_access_token():
    """
    Holt einen neuen App Access Token von der Twitch API.
    """
    global APP_ACCESS_TOKEN, TOKEN_EXPIRATION_TIME
    try:
        payload = {
            'client_id': settings.TWITCH_CLIENT_ID,
            'client_secret': settings.TWITCH_CLIENT_SECRET,
            'grant_type': 'client_credentials'
        }
        response = requests.post(TOKEN_URL, params=payload, timeout=15)
        response.raise_for_status()
        token_data = response.json()
        APP_ACCESS_TOKEN = token_data['access_token']
        # Setze die Ablaufzeit etwas früher, um Puffer zu haben (z.B. 1 Minute früher)
        TOKEN_EXPIRATION_TIME = datetime.now() + timedelta(seconds=token_data['expires_in'] - 60)
        logger.info("Successfully fetched new Twitch App Access Token.")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching Twitch App Access Token: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Twitch API Error Response: {e.response.text}")
        APP_ACCESS_TOKEN = None
        TOKEN_EXPIRATION_TIME = None
        return False
    except KeyError as e:
        logger.error(
            f"Error parsing token response: {e} - Response: {response.text if 'response' in locals() else 'N/A'}")
        APP_ACCESS_TOKEN = None
        TOKEN_EXPIRATION_TIME = None
        return False


def get_valid_app_access_token():
    """
    Gibt einen gültigen App Access Token zurück.
    Fordert einen neuen an, falls keiner vorhanden oder der aktuelle abgelaufen ist.
    """
    if not settings.TWITCH_CLIENT_ID or not settings.TWITCH_CLIENT_SECRET:
        logger.error("Twitch Client ID or Secret not configured in Django settings.")
        return None

    if APP_ACCESS_TOKEN and TOKEN_EXPIRATION_TIME and datetime.now() < TOKEN_EXPIRATION_TIME:
        return APP_ACCESS_TOKEN
    else:
        if _fetch_app_access_token():
            return APP_ACCESS_TOKEN
        else:
            return None


def get_user_id_by_login(twitch_login_name):
    """
    Holt die Twitch User ID für einen gegebenen Login-Namen.
    """
    token = get_valid_app_access_token()
    if not token:
        return None

    headers = {
        'Client-ID': settings.TWITCH_CLIENT_ID,
        'Authorization': f'Bearer {token}'
    }
    params = {'login': twitch_login_name}

    try:
        response = requests.get(HELIX_URL_USERS, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        if data.get('data'):
            return data['data'][0]['id']
        else:
            logger.warning(f"No user found with login name: {twitch_login_name}")
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching user ID for {twitch_login_name}: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Twitch API Error Response: {e.response.text}")
        return None
    except (IndexError, KeyError) as e:
        logger.error(f"Error parsing user data for {twitch_login_name}: {e} - Response: {data}")
        return None


def get_user_vods(twitch_user_id, max_results=10):
    """
    Holt die letzten VODs (Typ: "archive", also vergangene Übertragungen)
    eines Twitch-Nutzers anhand seiner User ID.
    """
    token = get_valid_app_access_token()
    if not token:
        return []

    headers = {
        'Client-ID': settings.TWITCH_CLIENT_ID,
        'Authorization': f'Bearer {token}'
    }
    params = {
        'user_id': twitch_user_id,
        'type': 'archive',
        # 'archive' für vergangene Übertragungen, 'upload' für manuelle Uploads, 'highlight' für Highlights
        'first': max_results,  # Anzahl der Ergebnisse
        'sort': 'time'  # Sortiert nach Datum (neueste zuerst)
    }

    try:
        response = requests.get(HELIX_URL_VIDEOS, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        videos_data = response.json().get('data', [])

        formatted_vods = []
        for video in videos_data:
            # Thumbnail URL: Ersetze Platzhalter für Größe
            thumbnail_url = video.get('thumbnail_url', '').replace('%{width}', '320').replace('%{height}', '180')

            # Dauer parsen (z.B. "1h2m3s" oder "45m10s")
            duration_str = video.get('duration', '0s')
            total_seconds = 0
            if 'h' in duration_str:
                parts = duration_str.split('h')
                total_seconds += int(parts[0]) * 3600
                duration_str = parts[1] if len(parts) > 1 else ''
            if 'm' in duration_str:
                parts = duration_str.split('m')
                total_seconds += int(parts[0]) * 60
                duration_str = parts[1] if len(parts) > 1 else ''
            if 's' in duration_str:
                parts = duration_str.split('s')
                total_seconds += int(parts[0])

            # Datum lesbarer formatieren
            try:
                created_at_dt = datetime.strptime(video.get('created_at'), "%Y-%m-%dT%H:%M:%SZ")
                created_at_formatted = created_at_dt.strftime("%d.%m.%Y, %H:%M Uhr")
            except (ValueError, TypeError):
                created_at_formatted = video.get('created_at')

            formatted_vods.append({
                'id': video.get('id'),
                'title': video.get('title'),
                'url': video.get('url'),
                'thumbnail_url': thumbnail_url,
                'duration_seconds': total_seconds,  # Für interne Nutzung/Sortierung
                'duration_formatted': f"{total_seconds // 3600:02d}:{(total_seconds % 3600) // 60:02d}:{total_seconds % 60:02d}",
                'created_at': created_at_formatted,
                'view_count': video.get('view_count')
            })
        logger.info(f"Successfully fetched {len(formatted_vods)} VODs for user ID {twitch_user_id}.")
        return formatted_vods
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching VODs for user ID {twitch_user_id}: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Twitch API Error Response: {e.response.text}")
        return []
    except Exception as e:
        logger.error(f"An unexpected error occurred while fetching or parsing VODs: {e}")
        return []