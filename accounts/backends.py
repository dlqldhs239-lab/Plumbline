from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailOrUsernameBackend(ModelBackend):
    """Let people sign in with the email they registered with. Falls back to
    the username so seeded and admin accounts keep working."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        if username is None or password is None:
            return None
        user = None
        if "@" in username:
            user = User.objects.filter(email__iexact=username.strip()).order_by("id").first()
        if user is None:
            user = User.objects.filter(username=username).first()
        if user is None:
            User().set_password(password)  # constant-time-ish: hash anyway
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
