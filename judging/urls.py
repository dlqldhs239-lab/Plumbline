from django.urls import path

from . import views

app_name = "judging"

urlpatterns = [
    path("", views.index, name="index"),
    path("<slug:slug>/", views.event_queue, name="queue"),
    path("<slug:slug>/review/<int:pk>/", views.review, name="review"),
]
