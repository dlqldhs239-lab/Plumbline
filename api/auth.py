"""API authentication.

Two ways in: a bearer token (what the checker and integrations use) or the
browser session (so the same endpoints work from the UI). Both resolve to a
Django user; role checks happen in the endpoints via events.permissions.

Session-authenticated unsafe requests must carry a CSRF token; bearer-token
requests do not, because a token in a header cannot be sent by a cross-site
form.
"""

from django.http import HttpRequest
from django.middleware.csrf import CsrfViewMiddleware
from ninja.security import APIKeyHeader

from accounts.models import ApiToken

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _csrf_ok(request: HttpRequest) -> bool:
    if request.method in SAFE_METHODS:
        return True
    middleware = CsrfViewMiddleware(lambda r: None)
    return middleware.process_view(request, None, (), {}) is None


class BearerOrSession(APIKeyHeader):
    param_name = "Authorization"
    openapi_description = "Bearer token from /accounts/tokens/. A logged-in browser session (with CSRF token) also works."

    def __call__(self, request: HttpRequest):
        header = request.headers.get(self.param_name, "")
        if header.lower().startswith("bearer "):
            user = ApiToken.authenticate(header[7:].strip())
            if user is not None:
                request.user = user
                return user
            return None
        if request.user.is_authenticated and _csrf_ok(request):
            return request.user
        return None

    def authenticate(self, request, key):  # pragma: no cover - replaced by __call__
        return None


class OptionalAuth(BearerOrSession):
    """Same resolution, but anonymous requests are allowed through with
    request.user left as AnonymousUser. Endpoints decide what they show."""

    def __call__(self, request: HttpRequest):
        user = super().__call__(request)
        if user is not None:
            return user
        if request.headers.get(self.param_name, ""):
            return None  # a bad token is an error, not anonymity
        return request.user


auth_required = BearerOrSession()
auth_optional = OptionalAuth()
