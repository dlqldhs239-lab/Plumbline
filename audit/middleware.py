import threading

_state = threading.local()


def current_request():
    return getattr(_state, "request", None)


class RequestActorMiddleware:
    """Keeps the current request reachable from the service layer so audit
    entries can record the actor and IP without every caller passing them."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _state.request = request
        try:
            return self.get_response(request)
        finally:
            _state.request = None
