from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness check for the load balancer and uptime monitoring."""
    return JsonResponse({"status": "ok"})
