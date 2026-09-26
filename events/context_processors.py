from django.conf import settings


def site(request):
    return {"SITE_NAME": settings.PLUMBLINE_SITE_NAME}
