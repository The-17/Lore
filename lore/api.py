import hashlib

from django.utils import timezone
from ninja import NinjaAPI
from ninja.errors import HttpError
from ninja.security import HttpBearer
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User

api = NinjaAPI(
    title="Lore API",
    description="The Artifact Plane — where AI work becomes reusable knowledge.",
    version="0.1.0",
)


@api.exception_handler(HttpError)
def on_http_error(request, exc: HttpError):
    return api.create_response(
        request,
        {"message": str(exc), "detail": str(exc), "status_code": exc.status_code},
        status=exc.status_code,
    )


from apps.accounts.auth import lore_auth

from apps.accounts.api import router as accounts_router  # noqa: E402
from apps.collections.api import router as collections_router  # noqa: E402
from apps.artifacts.api import router as artifacts_router  # noqa: E402
from apps.mcp.api import router as mcp_router  # noqa: E402

api.add_router("/auth", accounts_router)
api.add_router("/collections", collections_router, auth=lore_auth)
api.add_router("/artifacts", artifacts_router, auth=lore_auth)
api.add_router("/mcp", mcp_router, auth=lore_auth)


@api.get("/hello")
def hello(request):
    return {"message": "Welcome to Lore API"}
