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
    def list_versions(*, request, artifact_id: UUID) -> QuerySet[ArtifactVersion]:
        artifact = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=artifact_id)
        return artifact.versions.select_related("created_by").order_by("-version_number")

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
