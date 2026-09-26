from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("events/new/", views.event_create, name="event_create"),
    path("events/<slug:slug>/", views.event_detail, name="event_detail"),
    path("events/<slug:slug>/gallery/", views.gallery, name="gallery"),
    path("events/<slug:slug>/results/", views.results, name="results"),
    path("events/<slug:slug>/projects/<int:pk>/", views.project_detail, name="project_detail"),
    path("events/<slug:slug>/projects/new/", views.project_create, name="project_create"),
    path("events/<slug:slug>/projects/<int:pk>/edit/", views.project_edit, name="project_edit"),
    path("events/<slug:slug>/projects/<int:pk>/submit/", views.project_submit, name="project_submit"),
    path("events/<slug:slug>/projects/<int:pk>/withdraw/", views.project_withdraw, name="project_withdraw"),
    path("events/<slug:slug>/teams/new/", views.team_create, name="team_create"),
    path("events/<slug:slug>/teams/<int:pk>/", views.team_detail, name="team_detail"),
    path("events/<slug:slug>/teams/<int:pk>/invite/", views.team_invite, name="team_invite"),
    path("teams/join/<str:token>/", views.team_join, name="team_join"),
    # organizer console
    path("events/<slug:slug>/organize/", views.organize_dashboard, name="organize_dashboard"),
    path("events/<slug:slug>/organize/settings/", views.organize_settings, name="organize_settings"),
    path("events/<slug:slug>/organize/projects/", views.organize_projects, name="organize_projects"),
    path(
        "events/<slug:slug>/organize/projects/<int:pk>/hide/", views.organize_project_hide, name="organize_project_hide"
    ),
    path("events/<slug:slug>/organize/judges/", views.organize_judges, name="organize_judges"),
    path("events/<slug:slug>/organize/rubric/", views.organize_rubric, name="organize_rubric"),
    path("events/<slug:slug>/organize/assignments/", views.organize_assignments, name="organize_assignments"),
    path(
        "events/<slug:slug>/organize/assignments/<int:pk>/remove/",
        views.organize_assignment_remove,
        name="organize_assignment_remove",
    ),
    path("events/<slug:slug>/organize/results/", views.organize_results, name="organize_results"),
    path("events/<slug:slug>/organize/audit/", views.organize_audit, name="organize_audit"),
    path("events/<slug:slug>/organize/export/<str:kind>.csv", views.organize_export, name="organize_export"),
]
