from django.urls import path
# from django.contrib import admin # Wird hier nicht direkt gebraucht

# Importiere die Views aus der views.py im gleichen Ordner
from . import views

urlpatterns = [
    # Hauptseite (Login oder Dashboard) - verweist auf views.index
    path('', views.index, name='index'),

    # Admin Panel (Django's eingebautes) - Verweis ist OK, aber URL besser eindeutig
    # path('admin_panel/', admin.site.urls, name="admin_panel"), # Ist in highlights/urls.py schon unter /admin/

    # Detailseite für einen Stream - verweist auf views.stream
    path('stream/<int:stream_id>/', views.stream, name='stream'), # Hänge einen Slash an

    # Endpunkt zum Hinzufügen/Hochladen eines Streams - verweist auf views.add_stream
    path('add_stream/', views.add_stream, name='add_stream'),

    # Endpunkt zum Hinzufügen eines Clips (durch externen Server, aktuell ungenutzt) - verweist auf views.add_clip
    path('add_clip/', views.add_clip, name='add_clip'),

    # Endpunkt zum Löschen eines Streams - verweist auf views.delete_stream
    path('delete_stream/<str:user_name>/<str:stream_link>/', views.delete_stream, name='delete_stream'), # Hänge einen Slash an
]