from django.contrib import admin
from django.urls import include, path

import api.extras  # noqa: F401  (registers prizes, questions and import)
import api.integrations  # noqa: F401  (registers the webhook endpoints)
import api.organizer  # noqa: F401  (registers the organizer endpoints on the API)
import api.pairwise  # noqa: F401  (registers pairwise judging)
import api.records  # noqa: F401  (registers the record endpoints)
from api.router import api

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),
    path("accounts/", include("accounts.urls", namespace="accounts")),
    path("judge/", include("judging.urls", namespace="judging")),
    path("", include("community.urls")),
    path("", include("integrations.urls")),
    path("", include("records.urls")),
    path("", include("events.urls")),
]
