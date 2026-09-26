from django.urls import path

from . import views

urlpatterns = [
    path("events/<slug:slug>/ballot/", views.ballot, name="ballot"),
    path("events/<slug:slug>/ballot/<str:token>/", views.ballot, name="ballot_token"),
    path("events/<slug:slug>/projects/<int:pk>/vote/", views.vote, name="vote"),
    path("events/<slug:slug>/projects/<int:pk>/comments/", views.add_comment, name="add_comment"),
    path("events/<slug:slug>/projects/<int:pk>/comments/<int:comment_id>/hide/", views.hide_comment, name="hide_comment"),
    path("events/<slug:slug>/organize/voting/", views.organize_voting, name="organize_voting"),
    path("events/<slug:slug>/organize/voting/ballot-links.csv", views.export_ballot_links, name="export_ballot_links"),
    path("events/<slug:slug>/organize/voting/votes.csv", views.export_votes, name="export_votes"),
]
