from django.conf import settings


def site(request):
    user = getattr(request, "user", None)
    return {
        "SITE_NAME": settings.PLUMBLINE_SITE_NAME,
        # Told to the people who can do something about it, on every page.
        "SAMPLE_SECRETS": bool(
            settings.PLUMBLINE_SAMPLE_SECRETS and user is not None and user.is_authenticated and user.is_staff
        ),
    }
