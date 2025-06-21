# AI_Highlight_Clipper/highlights/settings.py

"""
Django settings for highlights project.
"""

from pathlib import Path
import os
# NEU: Importiere load_dotenv
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / ...
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- .env laden ---
dotenv_path = os.path.join(BASE_DIR, '.env')
# +++ START DEBUGGING .ENV +++
print(f"DEBUG: Attempting to load .env from: {dotenv_path}") # Pfad anzeigen
loaded = load_dotenv(dotenv_path=dotenv_path, verbose=True) # verbose=True für mehr Output von dotenv
print(f"DEBUG: load_dotenv() executed. File found and loaded: {loaded}") # Ergebnis prüfen (sollte True sein)
# +++ ENDE DEBUGGING .ENV +++
# --- Ende .env laden ---

# --- DIREKT danach Umgebung prüfen ---
print("-" * 20)
print(f"DEBUG: os.environ.get('TWITCH_CLIENT_ID'): {os.environ.get('TWITCH_CLIENT_ID')}")
# Secret nicht ganz ausgeben:
print(f"DEBUG: os.environ.get('TWITCH_CLIENT_SECRET') exists: {bool(os.environ.get('TWITCH_CLIENT_SECRET'))}")
print("-" * 20)
# --- ENDE ---


# Quick-start development settings - unsuitable for production
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'bu7^yq=0g5p1od(njuq&82i-$s4_0shd)_#me5euopf5$o8e99')
DEBUG = os.environ.get('DJANGO_DEBUG', 'True') == 'True'
allowed_hosts_str = os.environ.get('DJANGO_ALLOWED_HOSTS', '*')
ALLOWED_HOSTS = [host.strip() for host in allowed_hosts_str.split(',') if host.strip()]


# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'webapp.viewer.apps.ViewerConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'highlights.urls' # Zeigt auf highlights/urls.py

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'highlights.wsgi.application' # Zeigt auf highlights/wsgi.py


# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(BASE_DIR, 'db.sqlite3'), # DB im Hauptverzeichnis
    }
}


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    { 'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator', },
]


# Internationalization
LANGUAGE_CODE = 'de-de'
TIME_ZONE = 'Europe/Berlin'
USE_I18N = True
USE_L10N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
# STATICFILES_DIRS = [os.path.join(BASE_DIR, 'static')] # Ggf. hinzufügen

# Media files (User Uploads & Recordings)
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media') # media/ im Hauptverzeichnis

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
DATA_UPLOAD_MAX_NUMBER_FIELDS = 100_000

# --- Twitch API Credentials ---
# Werden jetzt aus os.environ gelesen (gefüllt durch load_dotenv() oben)
TWITCH_CLIENT_ID = os.environ.get('TWITCH_CLIENT_ID')
TWITCH_CLIENT_SECRET = os.environ.get('TWITCH_CLIENT_SECRET')

# Prüfung, ob die Keys jetzt tatsächlich geladen wurden (verursacht die Warnungen, wenn None)
if not TWITCH_CLIENT_ID:
    print("WARNUNG: TWITCH_CLIENT_ID nicht in .env oder Umgebungsvariablen gefunden!")
if not TWITCH_CLIENT_SECRET:
    print("WARNUNG: TWITCH_CLIENT_SECRET nicht in .env oder Umgebungsvariablen gefunden!")

# Pfad zum angepassten Recorder-Skript
TWITCH_RECORDER_SCRIPT_PATH = os.path.join(BASE_DIR, 'scripts', 'background_recorder.py')

# Prüfung, ob Pfad existiert
if not os.path.exists(TWITCH_RECORDER_SCRIPT_PATH):
    print(f"WARNUNG: TWITCH_RECORDER_SCRIPT_PATH nicht gefunden: {TWITCH_RECORDER_SCRIPT_PATH}")
# else: # Optionale Debug-Ausgabe für den Pfad
#    print(f"DEBUG settings.py - TWITCH_RECORDER_SCRIPT_PATH: {TWITCH_RECORDER_SCRIPT_PATH}")