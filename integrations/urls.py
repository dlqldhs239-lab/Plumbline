from django.urls import path

from . import views

urlpatterns = [
    path("events/<slug:slug>/organize/integrations/", views.organize_integrations, name="organize_integrations"),
]
