from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", auth_views.LoginView.as_view(template_name="accounts/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("signup/", views.signup, name="signup"),
    path("claim/<str:token>/", views.claim, name="claim"),
    path("password_reset/", views.password_help, name="password_help"),
    path(
        "password/",
        auth_views.PasswordChangeView.as_view(
            template_name="accounts/password_change.html", success_url="/accounts/password/done/"
        ),
        name="password_change",
    ),
    path(
        "password/done/",
        auth_views.PasswordChangeDoneView.as_view(template_name="accounts/password_change_done.html"),
        name="password_change_done",
    ),
    path("tokens/", views.tokens, name="tokens"),
    path("tokens/<int:pk>/revoke/", views.revoke_token, name="revoke_token"),
]
