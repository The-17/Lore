from datetime import datetime, timedelta
from uuid import UUID
from typing import Any

from django.contrib.auth import authenticate
from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from .models import User, Principal, AgentToken, Invite
from .selectors import UserSelector, InviteSelector, AgentTokenSelector

INVITE_EXPIRY_DAYS = 7


class AuthService:
    """User registration and authentication service."""

    @staticmethod
    def register_workspace_admin(*, email: str, first_name: str, last_name: str, password: str) -> tuple[User, dict[str, str]]:
        """Register the first human user as workspace admin."""
        with transaction.atomic():
            if UserSelector.exists():
                raise HttpError(403, "This workspace is invite-only. Contact your workspace admin for an invite link.")

            if UserSelector.get_by_email(email=email):
                raise HttpError(400, "User with this email already exists.")

            user = User.objects.create_user(
                email=email,
                first_name=first_name,
                last_name=last_name,
                password=password,
                is_workspace_admin=True,
            )
            # Create Principal for user
            Principal.objects.create(
                kind=Principal.Kind.USER,
                display_name=user.full_name,
                user=user,
            )
            tokens = user.tokens()
            return user, tokens

    @staticmethod
    def authenticate_user(*, email: str, password: str) -> tuple[User, dict[str, str]]:
        user = authenticate(email=email, password=password)
        if not user:
            raise HttpError(401, "Invalid email or password.")
        tokens = user.tokens()
        return user, tokens


class InviteService:
    """Workspace invite operations."""

    @staticmethod
    def create_invite(*, email: str, name: str, creator_user: User) -> Invite:
        if not creator_user.is_workspace_admin:
            raise HttpError(403, "Only the workspace admin can manage invites.")

        if UserSelector.get_by_email(email=email):
            raise HttpError(400, "A user with this email already exists.")

        with transaction.atomic():
            # Delete any prior unclaimed invite for the same email
            Invite.objects.filter(email=email, claimed=False).delete()

            token = Invite.generate_token()
            invite = Invite.objects.create(
                email=email,
                name=name,
                token=token,
                created_by=creator_user,
                expires_at=timezone.now() + timedelta(days=INVITE_EXPIRY_DAYS),
            )
            return invite

    @staticmethod
    def revoke_invite(*, invite_id: UUID, admin_user: User) -> None:
        if not admin_user.is_workspace_admin:
            raise HttpError(403, "Only the workspace admin can manage invites.")

        invite = InviteSelector.get_unclaimed_by_id(invite_id=invite_id)
        if not invite:
            raise HttpError(404, "Invite not found or already claimed.")
        invite.delete()

    @staticmethod
    def claim_invite(*, token: str, first_name: str, last_name: str, password: str) -> tuple[User, dict[str, str]]:
        with transaction.atomic():
            invite = InviteSelector.get_by_token(token=token)
            if not invite:
                raise HttpError(404, "Invite not found.")

            if invite.claimed:
                raise HttpError(400, "This invite has already been used.")

            if invite.expires_at < timezone.now():
                raise HttpError(400, "This invite has expired.")

            if UserSelector.get_by_email(email=invite.email):
                raise HttpError(400, "A user with this email already exists.")

            user = User.objects.create_user(
                email=invite.email,
                first_name=first_name,
                last_name=last_name,
                password=password,
            )

            Principal.objects.create(
                kind=Principal.Kind.USER,
                display_name=user.full_name,
                user=user,
            )

            invite.claimed = True
            invite.claimed_at = timezone.now()
            invite.save(update_fields=["claimed", "claimed_at"])

            tokens = user.tokens()
            return user, tokens


class AgentTokenService:
    """Agent token provisioning and revocation service."""

    @staticmethod
    def create_token(
        *,
        user: User,
        description: str,
        scope: str = "read_write",
        scopes: list[str] | None = None,
        restricted_collection_id: UUID | None = None,
        expires_in_days: int | None = 90,
        can_auto_approve: bool = False,
    ) -> tuple[AgentToken, str]:
        expires_at = None
        if expires_in_days:
            expires_at = timezone.now() + timedelta(days=expires_in_days)

        with transaction.atomic():
            token_obj, raw_token = AgentToken.generate(
                user=user,
                description=description,
                scope=scope,
                scopes=scopes,
                restricted_collection_id=restricted_collection_id,
                expires_at=expires_at,
                can_auto_approve=can_auto_approve,
            )
            return token_obj, raw_token

    @staticmethod
    def revoke_token(*, user: User, token_id: UUID) -> None:
        with transaction.atomic():
            token_obj = AgentTokenSelector.get_for_user(user=user, token_id=token_id)
            if not token_obj:
                raise HttpError(404, "Agent token not found.")
            token_obj.delete()
