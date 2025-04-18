# webapp/viewer/urls.py

from django.urls import path
# Importiere die eingebaute LogoutView
from django.contrib.auth.views import LogoutView
# Importiere deine Views
from . import views

urlpatterns = [
    # Hauptseite (Login oder Dashboard) - verweist auf views.index
    path('', views.index, name='index'),

    # --- NEUE URL für Logout ---
    # Beim Aufruf von /logout/ wird der User ausgeloggt und zu 'index' weitergeleitet
    path('logout/', LogoutView.as_view(next_page='index'), name='logout'),

    # Endpunkt zum Hinzufügen/Hochladen eines Streams - verweist auf views.add_stream
    path('add_stream/', views.add_stream, name='add_stream'),

    # --- NEUE URL für den Videoplayer ---
    path('video/<int:stream_id>/', views.video_player_view, name='video_player'),

    # --- NEUE URL für die Generator-Seite ---
    path('generator/<int:stream_id>/', views.generator_view, name='generator'),

    # Alte Detailseite für einen Stream (zeigt Highlights) - KANN WEG ODER BLEIBEN
    path('stream/<int:stream_id>/', views.stream, name='stream'),

    # Endpunkt zum Hinzufügen eines Clips (durch externen Server, aktuell ungenutzt) - verweist auf views.add_clip
    path('add_clip/', views.add_clip, name='add_clip'),

    # Endpunkt zum Löschen eines Streams - verweist auf views.delete_stream
    path('delete_stream/<str:user_name>/<str:stream_link>/', views.delete_stream, name='delete_stream'),
]