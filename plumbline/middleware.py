from django.db import DataError
from django.http import HttpResponseBadRequest, HttpResponseNotFound, JsonResponse
from django.template.loader import render_to_string


class OutOfRangeMiddleware:
    """A number too large for the database is a wrong address, not a crash.

    Services check their inputs; this is the net under them for ids in URLs
    (`/projects/99999999999999999999/`), which no view can pre-check.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not isinstance(exception, (OverflowError, DataError)):
            return None
        reading = request.method in ("GET", "HEAD")
        if request.path.startswith("/api/"):
            if reading:
                return JsonResponse({"detail": "Not found"}, status=404)
            return JsonResponse({"detail": "A value is out of range or too long."}, status=400)
        if reading:
            return HttpResponseNotFound(render_to_string("404.html", request=request))
        return HttpResponseBadRequest("A value is out of range or too long.")
