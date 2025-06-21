# AI_Highlight_Clipper/webapp/viewer/urls.py

from django.urls import path
from django.contrib.auth.views import LogoutView
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('logout/', LogoutView.as_view(next_page='index'), name='logout'),
    path('add_stream/', views.add_stream, name='add_stream'), # Upload
    path('record_stream/', views.record_stream_view, name='record_stream'), # Twitch Record Start
    path('stop_recording/<int:stream_id>/', views.stop_recording_view, name='stop_recording'),
    path('process_video/<int:stream_id>/', views.process_recorded_video_view, name='process_video'),
    path('video/<int:stream_id>/', views.video_player_view, name='video_player'),
    path('generator/<int:stream_id>/', views.generator_view, name='generator'),
    path('regenerate/<int:stream_id>/', views.regenerate_highlights_view, name='regenerate_highlights'),
    path('stream/<int:stream_id>/', views.stream, name='stream'), # Alte Highlight-Seite
    path('add_clip/', views.add_clip, name='add_clip'),
    path('delete/<int:stream_id>/', views.delete_stream, name='delete_stream'),
    path('infoviews/', views.info_views, name='infoviews'),

    # --- NEUE URLS für Twitch VOD Import ---
    path('fetch_twitch_vods/', views.fetch_twitch_vods_view, name='fetch_twitch_vods'),
    path('import_twitch_vod/<str:vod_id>/', views.import_selected_twitch_vod_view, name='import_selected_twitch_vod'),
    path('generate/<int:stream_id>/', views.generate_highlights_view, name='generate_highlights'),
    path('generator/<int:stream_id>/', views.generator_view, name='generator'),
]