from uuid import UUID
from typing import Optional
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from apps.common.security import scope_artifacts_queryset
from .models import (
    Artifact,
    ArtifactDraft,
    ArtifactVersion,
    ArtifactComment,
    ArtifactRelationship,
    ArtifactChunk,
)


class ArtifactSelector:
    """Read queries for artifacts, versions, and relationships."""

    @staticmethod
    def list_for_request(
        *,
        request,
        collection_id: Optional[UUID] = None,
        artifact_type: Optional[str] = None,
        query: str = "",
    ) -> QuerySet[Artifact]:
        qs = (
            Artifact.objects.filter(deleted_at__isnull=True)
            .select_related(
                "owner",
                "created_by",
                "current_version",
                "skill",
                "decision",
                "document",
                "memory",
                "draft",
            )
        )
        qs = scope_artifacts_queryset(qs, request)

        if collection_id:
            qs = qs.filter(collection_id=collection_id)
        elif not artifact_type:
            qs = qs.filter(collection__isnull=True)

        if artifact_type:
            qs = qs.filter(type=artifact_type)

        if query:
            qs = qs.filter(title__icontains=query)

        return qs.order_by("-updated_at")

    @staticmethod
    def get_by_id_for_request(*, request, artifact_id: UUID) -> Artifact:
        qs = (
            Artifact.objects.filter(deleted_at__isnull=True)
            .select_related(
                "owner",
                "created_by",
                "current_version",
                "skill",
                "decision",
                "document",
                "memory",
                "draft",
            )
        )
        qs = scope_artifacts_queryset(qs, request)
        return get_object_or_404(qs, id=artifact_id)

    @staticmethod
    def list_skills_for_request(*, request) -> QuerySet[Artifact]:
        qs = (
            Artifact.objects.filter(deleted_at__isnull=True, type="skill")
            .select_related("owner", "created_by", "current_version", "skill", "draft")
            .order_by("-updated_at")
        )
        return scope_artifacts_queryset(qs, request)

    @staticmethod
    def get_skill_by_title(*, request, title: str) -> Artifact:
        qs = (
            Artifact.objects.filter(deleted_at__isnull=True, type="skill", title__iexact=title)
            .select_related("owner", "created_by", "current_version", "skill")
        )
        qs = scope_artifacts_queryset(qs, request)
        return get_object_or_404(qs)

    @staticmethod
    def get_delta(*, request, artifact_id: UUID, since_version: int = 1) -> dict:
        artifact = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=artifact_id)
        versions = (
            artifact.versions.filter(version_number__gt=since_version)
            .order_by("version_number")
        )
        patches = [
            {
                "version_number": v.version_number,
                "commit_message": v.commit_message,
                "diff_content": v.diff_content,
                "created_at": v.created_at.isoformat(),
            }
            for v in versions
        ]
        current_ver = artifact.current_version.version_number if artifact.current_version else 1
        return {
            "artifact_id": str(artifact.id),
            "title": artifact.title,
            "current_version": current_ver,
            "since_version": since_version,
            "delta_count": len(patches),
            "patches": patches,
        }

    @staticmethod
    def get_version_diff(*, request, artifact_id: UUID, from_version: int, to_version: int) -> dict:
        from .utils import compute_diff
        artifact = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=artifact_id)
        v_from = artifact.versions.filter(version_number=from_version).first()
        v_to = artifact.versions.filter(version_number=to_version).first()
        if not v_from or not v_to:
            raise HttpError(404, "One or both specified versions were not found.")

        from_bytes = b""
        to_bytes = b""
        if v_from.file_instance:
            v_from.file_instance.seek(0)
            from_bytes = v_from.file_instance.read()
        if v_to.file_instance:
            v_to.file_instance.seek(0)
            to_bytes = v_to.file_instance.read()

        patch = compute_diff(from_bytes, to_bytes)
        return {
            "artifact_id": str(artifact.id),
            "from_version": from_version,
            "to_version": to_version,
            "diff": patch,
        }

    @staticmethod
    def list_versions(*, request, artifact_id: UUID) -> QuerySet[ArtifactVersion]:
        artifact = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=artifact_id)
        return artifact.versions.select_related("created_by").order_by("-version_number")

    @staticmethod
    def get_graph(*, request, collection_id: Optional[UUID] = None) -> dict:
        """
        Retrieves the complete directed knowledge graph for artifacts
        scoped to the caller's workspace permissions.
        """
        qs = Artifact.objects.filter(deleted_at__isnull=True)
        if collection_id:
            qs = qs.filter(collection_id=collection_id)
        qs = scope_artifacts_queryset(qs, request)

        artifact_rows = list(qs.values("id", "title", "type", "lifecycle_state", "collection_id"))
        artifact_ids = {a["id"] for a in artifact_rows}

        if not artifact_ids:
            return {"nodes": [], "edges": []}

        relationships = ArtifactRelationship.objects.filter(
            from_artifact_id__in=artifact_ids,
            to_artifact_id__in=artifact_ids,
        ).values("id", "from_artifact_id", "to_artifact_id", "relation_type", "created_at")

        nodes = [
            {
                "id": a["id"],
                "title": a["title"],
                "type": a["type"],
                "lifecycle_state": a["lifecycle_state"],
                "collection_id": a["collection_id"],
            }
            for a in artifact_rows
        ]

        edges = [
            {
                "id": r["id"],
                "source_id": r["from_artifact_id"],
                "target_id": r["to_artifact_id"],
                "relation_type": r["relation_type"],
                "created_at": r["created_at"],
            }
            for r in relationships
        ]

        return {"nodes": nodes, "edges": edges}

    @staticmethod
    def get_relationships(*, request, artifact_id: UUID) -> dict:
        artifact = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=artifact_id)
        outgoing = (
            artifact.outgoing_relationships.select_related("from_artifact", "to_artifact", "created_by")
            .all()
        )
        incoming = (
            artifact.incoming_relationships.select_related("from_artifact", "to_artifact", "created_by")
            .all()
        )
        return {"outgoing": outgoing, "incoming": incoming}

    @staticmethod
    def list_comments(*, request, artifact_id: UUID) -> QuerySet[ArtifactComment]:
        artifact = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=artifact_id)
        return artifact.comments.select_related("author").order_by("-created_at")

    @staticmethod
    def search_chunks(*, request, query: str, limit: int = 10) -> list[dict]:
        if not query:
            return []
        allowed_artifacts = scope_artifacts_queryset(
            Artifact.objects.filter(deleted_at__isnull=True), request
        )
        chunks = (
            ArtifactChunk.objects.filter(artifact__in=allowed_artifacts, text__icontains=query)
            .select_related("artifact", "version")[:limit]
        )
        return [
            {
                "chunk_id": str(c.id),
                "artifact_id": str(c.artifact.id),
                "artifact_title": c.artifact.title,
                "artifact_type": c.artifact.type,
                "version_number": c.version.version_number,
                "chunk_index": c.chunk_index,
                "text": c.text,
            }
            for c in chunks
        ]


class DraftSelector:
    """Read queries for artifact drafts."""

    @staticmethod
    def get_draft(*, request, artifact_id: UUID) -> ArtifactDraft | None:
        artifact = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=artifact_id)
        return (
            ArtifactDraft.objects.filter(artifact=artifact)
            .select_related("artifact", "last_edited_by")
            .prefetch_related("participants")
            .first()
        )
