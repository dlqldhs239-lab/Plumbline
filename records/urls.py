from django.urls import path

from . import views

urlpatterns = [
    path("verify/", views.verify, name="verify"),
    path("verify/key.json", views.key, name="verify_key"),
    path("records/<str:serial>.json", views.record_json, name="record_json"),
    path("records/<str:serial>/", views.record_detail, name="record_detail"),
    path("events/<slug:slug>/organize/records/", views.organize_records, name="organize_records"),
    path(
        "events/<slug:slug>/organize/records/<str:serial>/revoke/",
        views.organize_record_revoke,
        name="organize_record_revoke",
    ),
]
