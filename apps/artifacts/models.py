import uuid

from django.db import models

from apps.accounts.models import Principal
from apps.common.models import BaseModel


class ArtifactType(models.TextChoices):
    """
    Registry of artifact types.

    Only add entries here when an actual subtype table exists.
    Exposed to MCP clients via the ``list_artifact_types`` tool.
    """

    DOCUMENT = "document", "Document"
    SKILL = "skill", "Skill"
    DECISION = "decision", "Decision"
    MEMORY = "memory", "Memory"


class LifecycleState(models.TextChoices):
    DRAFT = "draft", "Draft"
    REVIEW = "review", "Review"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    PUBLISHED = "published", "Published"
    DEPRECATED = "deprecated", "Deprecated"
    ARCHIVED = "archived", "Archived"
    DELETED = "deleted", "Deleted"


class EmbeddingStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    INDEXED = "indexed", "Indexed"
    STALE = "stale", "Stale"
    FAILED = "failed", "Failed"


class RelationType(models.TextChoices):
    DERIVED_FROM = "derived_from", "Derived From"
    REFERENCES = "references", "References"
    USES = "uses", "Uses"
    PRODUCED_BY = "produced_by", "Produced By"
    DEPENDS_ON = "depends_on", "Depends On"
    SUPERSEDES = "supersedes", "Supersedes"
    REVIEWED_BY = "reviewed_by", "Reviewed By"
    USED_IN = "used_in", "Used In"


class PermissionRole(models.TextChoices):
    VIEWER = "viewer", "Viewer"
    EDITOR = "editor", "Editor"
    OWNER = "owner", "Owner"


# ---------------------------------------------------------------------------
# Core Artifact
# ---------------------------------------------------------------------------


class Artifact(BaseModel):
    """
    The primary object in Lore.

    Every artifact type (document, skill, decision, memory, …) inherits
    from this single lean base table.  Keep this table small — it is the
    join target for graph traversal across all types, so bloating it
    hurts performance everywhere.

    Type-specific fields belong in the corresponding extension table
    (``DocumentArtifact``, ``SkillArtifact``, etc.), not here.
    """

    type = models.CharField(max_length=50, choices=ArtifactType.choices)
    title = models.CharField(max_length=255)

    owner = models.ForeignKey(
        Principal, on_delete=models.CASCADE, related_name="owned_artifacts",
    )
    created_by = models.ForeignKey(
        Principal, on_delete=models.CASCADE, related_name="created_artifacts",
    )

    collection = models.ForeignKey(
        "collections.Collection", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="artifacts",
    )
    inherit_permissions = models.BooleanField(default=True)
    current_version = models.OneToOneField(
        "ArtifactVersion", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+",
    )

    lifecycle_state = models.CharField(
        max_length=20, choices=LifecycleState.choices,
        default=LifecycleState.DRAFT,
    )
    embedding_status = models.CharField(
        max_length=20, choices=EmbeddingStatus.choices,
        default=EmbeddingStatus.PENDING,
    )
    locked_by = models.ForeignKey(
        Principal, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="locked_artifacts",
    )
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["type"]),
            models.Index(fields=["lifecycle_state"]),
            models.Index(fields=["collection", "type"]),
        ]
        constraints = [
            # collection_id=NULL + inherit_permissions=True is forbidden.
            models.CheckConstraint(
                name="no_inherit_without_collection",
                check=~models.Q(collection__isnull=True, inherit_permissions=True),
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.type})"

    def resolve_permissions(self, principal):
        """
        Single-path permission resolver.

        If ``inherit_permissions`` is True, walk up the collection tree.
        Otherwise, check ``ArtifactPermission`` rows scoped to this
        artifact.

        Returns the ``PermissionRole`` value or None if no access.
        """
        if not self.inherit_permissions:
            perm = self.permissions.filter(principal=principal).first()
            return perm.role if perm else None

        # Walk up collections until a matching CollectionPermission is found.
        # For now, collection ownership implies full access.
        collection = self.collection
        while collection is not None:
            if collection.owner_id == principal.id:
                return PermissionRole.OWNER
            collection = collection.parent
        return None


# ---------------------------------------------------------------------------
# Type Extension Tables (1:1 to Artifact)
# ---------------------------------------------------------------------------


class DocumentArtifact(models.Model):
    """Extension table for type='document'."""

    artifact = models.OneToOneField(
        Artifact, on_delete=models.CASCADE, primary_key=True,
        related_name="document",
    )
    file = models.FileField(upload_to="files")
    format = models.CharField(
        max_length=20, default="markdown",
        help_text="markdown, pdf, docx, txt, etc.",
    )

    def __str__(self):
        return f"Document: {self.artifact.title}"


class SkillArtifact(models.Model):
    """Extension table for type='skill'."""

    artifact = models.OneToOneField(
        Artifact, on_delete=models.CASCADE, primary_key=True,
        related_name="skill",
    )
    skill_md_content = models.TextField(blank=True)
    usage_count = models.IntegerField(default=0)

    def __str__(self):
        return f"Skill: {self.artifact.title}"


class DecisionArtifact(models.Model):
    """Extension table for type='decision'."""

    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        DECIDED = "decided", "Decided"
        SUPERSEDED = "superseded", "Superseded"

    artifact = models.OneToOneField(
        Artifact, on_delete=models.CASCADE, primary_key=True,
        related_name="decision",
    )
    decision_text = models.TextField()
    rationale = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PROPOSED,
    )

    def __str__(self):
        return f"Decision: {self.artifact.title}"


class MemoryArtifact(models.Model):
    """Extension table for type='memory'."""

    artifact = models.OneToOneField(
        Artifact, on_delete=models.CASCADE, primary_key=True,
        related_name="memory",
    )
    content = models.TextField()
    scope = models.CharField(
        max_length=50, blank=True,
        help_text="agent_id, session_id, or collection-level",
    )
    expires_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Memory: {self.artifact.title}"


# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------


class ArtifactVersion(BaseModel):
    """
    Immutable version record.

    Every write creates a new row; never mutate a version in place.
    ``Artifact.current_version`` always points to the latest.
    For text/markdown content, full snapshots are stored per version;
    diffs are computed as a UI convenience.
    """

    artifact = models.ForeignKey(
        Artifact, on_delete=models.CASCADE, related_name="versions",
    )
    version_number = models.IntegerField()
    file_instance = models.FileField(upload_to="artifact_versions/")
    diff_content = models.TextField(blank=True)
    created_by = models.ForeignKey(
        Principal, on_delete=models.SET_NULL, null=True,
    )
    commit_message = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-version_number"]
        unique_together = [("artifact", "version_number")]

    def __str__(self):
        return f"{self.artifact.title} v{self.version_number}"


# ---------------------------------------------------------------------------
# Artifact Graph
# ---------------------------------------------------------------------------


class ArtifactRelationship(models.Model):
    """
    Directed edge in the Artifact Graph.

    Independent of ``collection_id`` — graph relationships are not
    constrained by folder structure.  Always stores ``created_at`` and
    ``created_by`` for provenance-over-time.
    """

    id = models.UUIDField(default=uuid.uuid4, unique=True, primary_key=True)
    from_artifact = models.ForeignKey(
        Artifact, on_delete=models.CASCADE, related_name="outgoing_relationships",
    )
    to_artifact = models.ForeignKey(
        Artifact, on_delete=models.CASCADE, related_name="incoming_relationships",
    )
    relation_type = models.CharField(
        max_length=50, choices=RelationType.choices,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        Principal, on_delete=models.CASCADE,
    )

    class Meta:
        indexes = [
            models.Index(fields=["from_artifact", "relation_type"]),
            models.Index(fields=["to_artifact", "relation_type"]),
        ]

    def __str__(self):
        return f"{self.from_artifact} —[{self.relation_type}]→ {self.to_artifact}"


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


class ArtifactPermission(models.Model):
    """
    Explicit ACL override for an individual artifact.

    Only used when ``artifact.inherit_permissions`` is False.
    When True, permissions resolve from the collection hierarchy.
    """

    id = models.UUIDField(default=uuid.uuid4, unique=True, primary_key=True)
    artifact = models.ForeignKey(
        Artifact, on_delete=models.CASCADE, related_name="permissions",
    )
    principal = models.ForeignKey(
        Principal, on_delete=models.CASCADE,
    )
    role = models.CharField(max_length=20, choices=PermissionRole.choices)

    class Meta:
        unique_together = [("artifact", "principal")]

    def __str__(self):
        return f"{self.principal} → {self.artifact} ({self.role})"


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


class ArtifactComment(BaseModel):
    """Comment on an artifact, replacing the old File-scoped Comment."""

    artifact = models.ForeignKey(
        Artifact, on_delete=models.CASCADE, related_name="comments",
    )
    author = models.ForeignKey(
        Principal, on_delete=models.CASCADE,
    )
    body = models.TextField()

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Comment on {self.artifact.title} by {self.author}"


# ---------------------------------------------------------------------------
# Embeddings / Chunks
# ---------------------------------------------------------------------------


class ArtifactChunk(models.Model):
    """
    Chunked text for semantic search via pgvector.

    Chunks are kept per-version (not overwritten) to support
    time-aware search and semantic diffing later.
    """

    id = models.UUIDField(default=uuid.uuid4, unique=True, primary_key=True)
    artifact = models.ForeignKey(
        Artifact, on_delete=models.CASCADE, related_name="chunks",
    )
    version = models.ForeignKey(
        ArtifactVersion, on_delete=models.CASCADE, related_name="chunks",
    )
    chunk_index = models.IntegerField()
    text = models.TextField()
    # embedding field will be added when pgvector extension is installed:
    # embedding = VectorField(dimensions=1536)

    class Meta:
        ordering = ["chunk_index"]
        unique_together = [("version", "chunk_index")]

    def __str__(self):
        return f"Chunk {self.chunk_index} of {self.artifact.title}"


class ArtifactDraft(BaseModel):
    """
    Mutable working draft for an artifact.

    The single source of truth during editing is ``block_data`` — a JSON
    array of ADM Block objects.  Markdown is generated from block_data
    only at commit time.

    A draft belongs to the artifact, not to a person.  Multiple humans
    and agents can contribute to the same draft simultaneously.
    """

    artifact = models.OneToOneField(
        Artifact, on_delete=models.CASCADE, related_name="draft",
    )
    block_data = models.JSONField(
        default=list,
        help_text="ADM Block[] — the canonical document during editing.",
    )
    participants = models.ManyToManyField(
        Principal, blank=True, related_name="draft_participations",
        help_text="Principals who have contributed edits to this draft.",
    )
    last_edited_by = models.ForeignKey(
        Principal, on_delete=models.SET_NULL, null=True,
        related_name="last_edited_drafts",
    )
    commit_msg_hint = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Draft of {self.artifact.title}"
