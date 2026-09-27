from django.contrib import admin
from django.urls import include, path

import api.integrations  # noqa: F401  (registers the webhook endpoints)
import api.organizer  # noqa: F401  (registers the organizer endpoints on the API)
from api.router import api

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),
    path("accounts/", include("accounts.urls", namespace="accounts")),
    path("judge/", include("judging.urls", namespace="judging")),
    path("", include("community.urls")),
    path("", include("integrations.urls")),
    path("", include("events.urls")),
]
