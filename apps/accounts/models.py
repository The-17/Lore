import hashlib
import hmac
import secrets
import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils.translation import gettext_lazy as _
from rest_framework_simplejwt.tokens import RefreshToken

from .managers import CustomUserManager


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(default=uuid.uuid4, unique=True, primary_key=True)
    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)

    email = models.EmailField(_('Email Address'), unique=True)
    avatar= models.ImageField(upload_to='avatars/', null=True, blank=True)
    is_agent = models.BooleanField(default=False)
    is_workspace_admin = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_superuser = models.BooleanField(default=False)
    is_email_verified = models.BooleanField(default=False)
    terms_agreement = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects =CustomUserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    def __str__(self):
        return self.full_name

    def tokens(self):
        refresh = RefreshToken.for_user(self)
        principal_id = None
        display_name = self.full_name
        if hasattr(self, "principal") and self.principal:
            principal_id = str(self.principal.id)
            display_name = self.principal.display_name
        
        # Inject custom claims into token payload for Zero-DB Ingress
        refresh["user_id"] = str(self.id)
        refresh["principal_id"] = principal_id
        refresh["display_name"] = display_name
        refresh["email"] = self.email
        refresh["is_workspace_admin"] = self.is_workspace_admin
        refresh["roles"] = ["owner", "admin"] if self.is_workspace_admin else ["member"]

        access_token = refresh.access_token
        access_token["user_id"] = str(self.id)
        access_token["principal_id"] = principal_id
        access_token["display_name"] = display_name
        access_token["email"] = self.email
        access_token["is_workspace_admin"] = self.is_workspace_admin
        access_token["roles"] = ["owner", "admin"] if self.is_workspace_admin else ["member"]

        return {
            'refresh': str(refresh),
            'access': str(access_token)
        }


class Principal(models.Model):
    """
    Unified identity for all actors in Lore.

    Every entity that can own, create, modify, or be granted permissions
    to an artifact resolves to a single Principal row.  This eliminates
    polymorphic FKs throughout the schema — ``Artifact.owner``,
    ``ArtifactRelationship.created_by``, ``ArtifactPermission.principal``
    all point here.

    Principals are created directly in the registration, invite-claim,
    and agent-token-create views — not via signals.
    """

    class Kind(models.TextChoices):
        USER = "user", "User"
        AGENT_TOKEN = "agent_token", "Agent Token"

    id = models.UUIDField(default=uuid.uuid4, unique=True, primary_key=True)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    display_name = models.CharField(max_length=255)
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, null=True, blank=True,
        related_name="principal",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.display_name} ({self.kind})"


class AgentToken(models.Model):
    """
    Scoped high-entropy API key for AI agent and MCP access.

    Format: ``lore_agt_<lookup_id>_<secret_entropy>``.
    The 8-character lookup_id provides indexed O(1) row retrieval.
    Only the SHA-256 hash is persisted; verification uses constant-time
    HMAC comparisons to prevent side-channel timing attacks.
    """

    id = models.UUIDField(default=uuid.uuid4, unique=True, primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="agent_tokens")
    principal = models.OneToOneField(
        Principal, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="agent_token",
    )
    lookup_id = models.CharField(max_length=8, null=True, blank=True, db_index=True)
    token_hash = models.CharField(max_length=64, unique=True, db_index=True, default="")
    token_prefix = models.CharField(max_length=20, default="")
    description = models.TextField(blank=True)
    scope = models.CharField(
        max_length=20,
        default="read_write",
        choices=[("read_only", "Read Only"), ("read_write", "Read Write")],
    )
    scopes = models.JSONField(default=list, blank=True, help_text="Fine-grained permission scopes")
    restricted_collection = models.ForeignKey(
        "collections.Collection", on_delete=models.SET_NULL, null=True, blank=True,
    )
    can_auto_approve = models.BooleanField(
        default=False,
        help_text=_("Allows pre-reviewed pipeline agents to create artifacts directly in approved or published states."),
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def verify_secret(self, raw_token_or_secret: str) -> bool:
        """Constant-time hash comparison to prevent timing side-channel attacks."""
        import hmac
        # If full token passed, hash the entire token (for backwards compatibility) or secret part
        computed_hash = hashlib.sha256(raw_token_or_secret.encode("utf-8")).hexdigest()
        return hmac.compare_digest(self.token_hash, computed_hash)

    @classmethod
    def generate(
        cls,
        *,
        user: User,
        description: str,
        scope: str = "read_write",
        scopes: list[str] | None = None,
        restricted_collection=None,
        restricted_collection_id=None,
        expires_at=None,
        can_auto_approve: bool = False,
    ) -> tuple["AgentToken", str]:
        """Generate high-entropy prefixed agent token."""
        lookup_id = secrets.token_hex(4)  # 8 hex chars
        secret_entropy = secrets.token_urlsafe(32)
        raw_token = f"lore_agt_{lookup_id}_{secret_entropy}"
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        token_prefix = f"lore_agt_{lookup_id}"

        principal = Principal.objects.create(
            kind=Principal.Kind.AGENT_TOKEN,
            display_name=f"Agent: {description[:30]}",
        )

        token_obj = cls.objects.create(
            user=user,
            principal=principal,
            lookup_id=lookup_id,
            token_hash=token_hash,
            token_prefix=token_prefix,
            description=description,
            scope=scope,
            scopes=scopes or ([scope] if scope else []),
            restricted_collection=restricted_collection,
            restricted_collection_id=restricted_collection_id,
            can_auto_approve=can_auto_approve,
            expires_at=expires_at,
        )
        return token_obj, raw_token

    def __str__(self):
        return f"{self.user.email} - {self.description[:30]} ({self.scope})"


class Invite(models.Model):
    """
    Single-use invite token for provisioning human collaborators.

    The workspace admin generates an invite for a specific email.
    The invitee clicks the link, claims their account (name + password),
    and the invite is marked as consumed.  Unclaimed invites expire
    after the configured duration (default: 7 days).
    """

    id = models.UUIDField(default=uuid.uuid4, primary_key=True)
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=150, blank=True)  # optional display name hint
    token = models.CharField(max_length=64, unique=True, db_index=True)
    created_by = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="sent_invites",
    )
    claimed = models.BooleanField(default=False)
    claimed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        status = "claimed" if self.claimed else "pending"
        return f"Invite({self.email}, {status})"

    @staticmethod
    def generate_token():
        """Generate a cryptographically secure URL-safe invite token."""
        return secrets.token_urlsafe(32)

