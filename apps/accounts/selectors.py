from uuid import UUID
from django.db.models import QuerySet
from .models import User, Principal, AgentToken, Invite


class UserSelector:
    """Read-only selectors for User accounts."""

    @staticmethod
    def get_by_id(*, user_id: UUID) -> User | None:
        return User.objects.filter(id=user_id).select_related("principal").first()

    @staticmethod
    def get_by_email(*, email: str) -> User | None:
        return User.objects.filter(email=email).select_related("principal").first()

    @staticmethod
    def exists() -> bool:
        return User.objects.exists()


class AgentTokenSelector:
    """Read-only selectors for Agent Tokens."""

    @staticmethod
    def list_for_user(*, user: User) -> QuerySet[AgentToken]:
        return (
            AgentToken.objects.filter(user=user)
            .select_related("principal", "restricted_collection")
            .order_by("-created_at")
        )

    @staticmethod
    def get_for_user(*, user: User, token_id: UUID) -> AgentToken | None:
        return (
            AgentToken.objects.filter(id=token_id, user=user)
            .select_related("principal", "restricted_collection")
            .first()
        )

    @staticmethod
    def get_by_lookup_id(*, lookup_id: str) -> AgentToken | None:
        return (
            AgentToken.objects.filter(lookup_id=lookup_id)
            .select_related("user", "principal", "restricted_collection")
            .first()
        )


class InviteSelector:
    """Read-only selectors for Workspace Invites."""

    @staticmethod
    def list_invites() -> QuerySet[Invite]:
        return Invite.objects.all().order_by("-created_at")

    @staticmethod
    def get_by_token(*, token: str) -> Invite | None:
        return Invite.objects.filter(token=token).first()

    @staticmethod
    def get_unclaimed_by_id(*, invite_id: UUID) -> Invite | None:
        return Invite.objects.filter(id=invite_id, claimed=False).first()
