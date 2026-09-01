from datetime import timedelta
from uuid import UUID

from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from .auth import lore_auth
from .schemas import (
    AgentTokenCreateResponseSchema,
    AgentTokenCreateSchema,
    AgentTokenResponseSchema,
    InviteClaimSchema,
    InviteCreateSchema,
    InviteListSchema,
    InvitePreviewSchema,
    InviteResponseSchema,
    LoginSchema,
    RegisterSchema,
)
from .selectors import InviteSelector, AgentTokenSelector
from .services import AuthService, InviteService, AgentTokenService

router = Router(tags=["Authentication"])


# ---------------------------------------------------------------------------
# Workspace Initialization (first-user-is-admin)
# ---------------------------------------------------------------------------


@router.post("/register", response={201: dict, 400: dict, 403: dict})
def register(request, data: RegisterSchema):
    """
    Register the workspace admin.
    Thin controller: delegates atomic workspace provisioning to AuthService.
    """
    user, tokens = AuthService.register_workspace_admin(
        email=data.email,
        first_name=data.first_name,
        last_name=data.last_name,
        password=data.password,
    )
    return 201, {
        "message": f"Workspace initialized. Welcome, {user.first_name}. You are the workspace admin.",
        "data": {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "access_token": tokens["access"],
            "refresh_token": tokens["refresh"],
        },
    }


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@router.post("/login", response={200: dict, 401: dict})
def login(request, data: LoginSchema):
    """
    Authenticate a human user and return dual JWT access/refresh token pair.
    Thin controller: delegates authentication to AuthService.
    """
    user, tokens = AuthService.authenticate_user(email=data.email, password=data.password)
    return 200, {
        "status": "success",
        "message": "Login successful",
        "data": {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "access_token": tokens["access"],
            "refresh_token": tokens["refresh"],
        },
    }


# ---------------------------------------------------------------------------
# Invite Management (workspace admin only)
# ---------------------------------------------------------------------------


@router.post("/invites", auth=lore_auth, response={201: InviteResponseSchema, 400: dict, 403: dict})
def create_invite(request, data: InviteCreateSchema):
    """Issue an invite link for a new collaborator (admin only)."""
    invite = InviteService.create_invite(
        email=data.email,
        name=data.name,
        creator_user=request.user,
    )
    return 201, invite


@router.get("/invites", auth=lore_auth, response=list[InviteListSchema])
def list_invites(request):
    """List all invites (admin only)."""
    if not getattr(request.user, "is_workspace_admin", False):
        raise HttpError(403, "Only the workspace admin can manage invites.")

    invites = InviteSelector.list_invites()
    return [
        InviteListSchema(
            id=inv.id,
            email=inv.email,
            name=inv.name,
            token_prefix=inv.token[:8],
            expires_at=inv.expires_at,
            claimed=inv.claimed,
            claimed_at=inv.claimed_at,
            created_at=inv.created_at,
        )
        for inv in invites
    ]


@router.delete("/invites/{invite_id}", auth=lore_auth, response={204: None, 403: dict, 404: dict})
def revoke_invite(request, invite_id: UUID):
    """Revoke an unclaimed invite (admin only)."""
    InviteService.revoke_invite(invite_id=invite_id, admin_user=request.user)
    return 204, None


# ---------------------------------------------------------------------------
# Invite Validation & Claim (unauthenticated)
# ---------------------------------------------------------------------------


@router.get("/invites/{token}/preview", response={200: InvitePreviewSchema, 400: dict, 404: dict})
def preview_invite(request, token: str):
    """Validate an invite token and return public preview for pre-filling claim form."""
    invite = InviteSelector.get_by_token(token=token)
    if not invite:
        return 404, {"message": "Invite not found."}
    if invite.claimed:
        return 400, {"message": "This invite has already been used."}
    if invite.expires_at < timezone.now():
        return 400, {"message": "This invite has expired."}

    return 200, InvitePreviewSchema(
        email=invite.email,
        name=invite.name,
        expires_at=invite.expires_at,
    )


@router.post("/invites/{token}/claim", response={201: dict, 400: dict, 404: dict})
def claim_invite(request, token: str, data: InviteClaimSchema):
    """Claim an invite and provision new user account."""
    user, tokens = InviteService.claim_invite(
        token=token,
        first_name=data.first_name,
        last_name=data.last_name,
        password=data.password,
    )
    return 201, {
        "message": f"Welcome to the workspace, {user.first_name}.",
        "data": {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "access_token": tokens["access"],
            "refresh_token": tokens["refresh"],
        },
    }


# ---------------------------------------------------------------------------
# Scoped Agent Token Endpoints
# ---------------------------------------------------------------------------


@router.post("/tokens", auth=lore_auth, response={201: AgentTokenCreateResponseSchema})
def create_agent_token(request, data: AgentTokenCreateSchema):
    """Provision high-entropy prefixed AgentToken for AI agent workloads."""
    token_obj, raw_token = AgentTokenService.create_token(
        user=request.user,
        description=data.description,
        scope=data.scope,
        restricted_collection_id=data.restricted_collection_id,
        expires_in_days=data.expires_in_days,
    )
    token_obj.token = raw_token  # Set transiently for response serialization
    return 201, token_obj


@router.get("/tokens", auth=lore_auth, response=list[AgentTokenResponseSchema])
def list_agent_tokens(request):
    """List agent tokens owned by active user."""
    return AgentTokenSelector.list_for_user(user=request.user)


@router.delete("/tokens/{token_id}", auth=lore_auth, response={204: None, 404: dict})
def delete_agent_token(request, token_id: UUID):
    """Revoke an agent token."""
    AgentTokenService.revoke_token(user=request.user, token_id=token_id)
    return 204, None
