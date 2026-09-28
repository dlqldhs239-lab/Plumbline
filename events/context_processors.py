from django.conf import settings

# The accounts the fixture set brings, as a visitor to a sample installation
# would want to try them. The password is printed in the README.
SAMPLE_LOGINS = (
    ("organizer@example.org", "Organizer", "Runs both events: the console, the results, the audit log."),
    ("wei.lindqvist@example.org", "Judge", "Has reviews waiting in Open House, and pairs to compare."),
    ("tomas.varga@example.org", "Judge", "A second judge, to see that neither can read the other."),
    ("priya1@example.org", "Participant", "On a team in Sample Hack; free to form one in Open House."),
    ("admin@example.org", "Administrator", "Everything, and the Django admin."),
)


def site(request):
    user = getattr(request, "user", None)
    sample = bool(settings.PLUMBLINE_SAMPLE_SECRETS)
    out = {
        "SITE_NAME": settings.PLUMBLINE_SITE_NAME,
        # Told to the people who can do something about it, on every page.
        "SAMPLE_SECRETS": bool(sample and user is not None and user.is_authenticated and user.is_staff),
    }
    if sample and request.path.startswith("/accounts/login"):
        from django.contrib.auth import get_user_model

        there = set(
            get_user_model()
            .objects.filter(email__in=[e for e, _, _ in SAMPLE_LOGINS], is_active=True)
            .values_list("email", flat=True)
        )
        out["SAMPLE_LOGINS"] = [{"email": e, "role": r, "note": n} for e, r, n in SAMPLE_LOGINS if e in there]
    return out
