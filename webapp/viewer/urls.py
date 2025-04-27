# AI_Highlight_Clipper/webapp/viewer/urls.py

from django.urls import path
from django.contrib.auth.views import LogoutView
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('logout/', LogoutView.as_view(next_page='index'), name='logout'),
    path('add_stream/', views.add_stream, name='add_stream'),
    path('video/<int:stream_id>/', views.video_player_view, name='video_player'),
    path('generator/<int:stream_id>/', views.generator_view, name='generator'),

    # --- NEUE URL für Re-Generierung ---
    path('regenerate/<int:stream_id>/', views.regenerate_highlights_view, name='regenerate_highlights'),

    # Alte Detailseite (Highlights)
    path('stream/<int:stream_id>/', views.stream, name='stream'),
    # Add Clip (veraltet)
    path('add_clip/', views.add_clip, name='add_clip'),
    # Delete Stream
    path('delete/<int:stream_id>/', views.delete_stream, name='delete_stream'),
]