from django.db import DataError, OperationalError
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound, JsonResponse
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
        if isinstance(exception, OperationalError) and "locked" in str(exception).lower():
            # SQLite, used for development, lets one writer in at a time and
            # turns the others away. That is "try again", not a fault.
            busy = "The database is busy. Try again in a moment."
            if request.path.startswith("/api/"):
                response = JsonResponse({"detail": busy}, status=503)
            else:
                response = HttpResponse(busy, status=503, content_type="text/plain; charset=utf-8")
            response["Retry-After"] = "1"
            return response
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
