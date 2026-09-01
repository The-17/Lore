import json
from uuid import UUID
from queue import Empty

from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from ninja import File, Router, UploadedFile
from ninja.errors import HttpError

from apps.accounts.auth import lore_auth
from apps.common.realtime import subscribe_events, unsubscribe_events

from .schemas import (
    ArtifactCommitSchema,
    ArtifactCreateSchema,
    ArtifactDraftResponseSchema,
    ArtifactResponseSchema,
    ArtifactUpdateSchema,
    ArtifactVersionResponseSchema,
    CommentResponseSchema,
    DraftPatchSchema,
    RelationshipCreateSchema,
    RelationshipResponseSchema,
)
from .selectors import ArtifactSelector, DraftSelector
from .services import (
    ArtifactService,
    CommentService,
    DraftService,
    RelationshipService,
    _initial_lifecycle_state,
)

router = Router(tags=["Artifacts & Graph"])


# ---------------------------------------------------------------------------
# Real-Time SSE Stream
# ---------------------------------------------------------------------------


@router.get("/stream/events")
def event_stream(request):
    """Server-Sent Events stream delivering real-time artifact and draft notifications."""
    principal = getattr(request, "principal", None)
    if principal is None:
        return HttpResponse(
            '{"message": "Authentication required for the event stream."}',
            status=401,
            content_type="application/json",
        )
    principal_id = str(principal.id)

    def event_generator():
        q = subscribe_events()
        try:
            yield 'event: connected\ndata: {"status": "connected"}\n\n'
            while True:
                try:
                    event_data = q.get(timeout=25)
                    if event_data.get("owner_id") != principal_id:
                        continue
                    yield f"event: {event_data['event']}\ndata: {json.dumps(event_data)}\n\n"
                except Empty:
                    yield 'event: ping\ndata: {"ping": true}\n\n'
        finally:
            unsubscribe_events(q)

    response = StreamingHttpResponse(event_generator(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


# ---------------------------------------------------------------------------
# Artifact Queries & Mutations
# ---------------------------------------------------------------------------


@router.post("/", response={201: ArtifactResponseSchema, 400: dict, 403: dict})
def create_artifact(request, data: ArtifactCreateSchema):
    """Create a new text-based artifact (skill, decision, memory)."""
    artifact = ArtifactService.create_text_artifact(request=request, data=data)
    return 201, ArtifactResponseSchema.from_model(artifact)


@router.post("/upload", response={201: ArtifactResponseSchema, 400: dict, 403: dict})
def upload_document(
    request,
    file: UploadedFile = File(...),  # noqa: B008
    collection_id: UUID | None = None,
    lifecycle_state: str = "draft",
):
    """Upload a document artifact."""
    artifact = ArtifactService.upload_document(
        request=request,
        file=file,
        collection_id=collection_id,
        lifecycle_state=lifecycle_state,
    )
    return 201, ArtifactResponseSchema.from_model(artifact)


@router.get("/", response=list[ArtifactResponseSchema])
def list_artifacts(request, collection_id: UUID | None = None, type: str | None = None, query: str = ""):
    """List artifacts within caller's scope."""
    artifacts = ArtifactSelector.list_for_request(
        request=request, collection_id=collection_id, artifact_type=type, query=query
    )
    return [ArtifactResponseSchema.from_model(a) for a in artifacts]


@router.get("/skills/list", response=list[ArtifactResponseSchema])
def list_skills(request):
    """List all available skill artifacts."""
    skills = ArtifactSelector.list_skills_for_request(request=request)
    return [ArtifactResponseSchema.from_model(s) for s in skills]


@router.get("/skills/{title}", response={200: dict, 404: dict})
def fetch_skill(request, title: str):
    """Fetch skill by title and increment usage count."""
    artifact = ArtifactSelector.get_skill_by_title(request=request, title=title)
    if hasattr(artifact, "skill"):
        skill = artifact.skill
        skill.usage_count += 1
        skill.save(update_fields=["usage_count"])
        content = skill.skill_md_content
    else:
        content = ""

    return 200, {
        "id": str(artifact.id),
        "title": artifact.title,
        "collection_id": str(artifact.collection_id) if artifact.collection_id else None,
        "usage_count": artifact.skill.usage_count if hasattr(artifact, "skill") else 0,
        "content": content,
        "lifecycle_state": artifact.lifecycle_state,
        "updated_at": artifact.updated_at.isoformat(),
    }


@router.get("/chunks/search", response=list[dict])
def search_chunks(request, query: str = "", limit: int = 10):
    """Search granular text chunks for RAG queries."""
    return ArtifactSelector.search_chunks(request=request, query=query, limit=limit)


@router.get("/{artifact_id}", response={200: ArtifactResponseSchema, 404: dict})
def get_artifact(request, artifact_id: UUID):
    """Retrieve an artifact by ID."""
    artifact = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=artifact_id)
    return 200, ArtifactResponseSchema.from_model(artifact)


@router.patch("/{artifact_id}", response={200: ArtifactResponseSchema, 403: dict, 404: dict, 409: dict})
def update_artifact(request, artifact_id: UUID, data: ArtifactUpdateSchema):
    """Update metadata or content of an artifact."""
    artifact = ArtifactService.update_artifact(request=request, artifact_id=artifact_id, data=data)
    return 200, ArtifactResponseSchema.from_model(artifact)


@router.delete("/{artifact_id}", response={204: None, 403: dict, 404: dict})
def delete_artifact(request, artifact_id: UUID):
    """Soft-delete an artifact."""
    ArtifactService.delete_artifact(request=request, artifact_id=artifact_id)
    return 204, None


# ---------------------------------------------------------------------------
# Governance & Locking
# ---------------------------------------------------------------------------


@router.post("/{artifact_id}/lock", response={200: ArtifactResponseSchema, 400: dict, 403: dict})
def lock_artifact(request, artifact_id: UUID):
    """Acquire an exclusive lock on an artifact."""
    artifact = ArtifactService.lock_artifact(request=request, artifact_id=artifact_id)
    return 200, ArtifactResponseSchema.from_model(artifact)


@router.post("/{artifact_id}/unlock", response={200: ArtifactResponseSchema, 400: dict, 403: dict})
def unlock_artifact(request, artifact_id: UUID):
    """Release an exclusive lock on an artifact."""
    artifact = ArtifactService.unlock_artifact(request=request, artifact_id=artifact_id)
    return 200, ArtifactResponseSchema.from_model(artifact)


@router.post("/{artifact_id}/approve", response={200: ArtifactResponseSchema, 403: dict, 404: dict})
def approve_artifact(request, artifact_id: UUID):
    """Approve an artifact proposal (human reviewer only)."""
    artifact = ArtifactService.approve_artifact(request=request, artifact_id=artifact_id)
    return 200, ArtifactResponseSchema.from_model(artifact)


@router.post("/{artifact_id}/reject", response={200: ArtifactResponseSchema, 403: dict, 404: dict})
def reject_artifact(request, artifact_id: UUID):
    """Reject an artifact proposal (human reviewer only)."""
    artifact = ArtifactService.reject_artifact(request=request, artifact_id=artifact_id)
    return 200, ArtifactResponseSchema.from_model(artifact)


# ---------------------------------------------------------------------------
# Version History & Deltas
# ---------------------------------------------------------------------------


@router.get("/{artifact_id}/versions", response=list[ArtifactVersionResponseSchema])
def list_artifact_versions(request, artifact_id: UUID):
    """List historical version snapshots for an artifact."""
    versions = ArtifactSelector.list_versions(request=request, artifact_id=artifact_id)
    return [ArtifactVersionResponseSchema.from_model(v) for v in versions]


@router.get("/{artifact_id}/delta", response={200: dict, 404: dict})
def get_artifact_delta(request, artifact_id: UUID, since_version: int = 1):
    """Retrieve incremental diff patches since a specified version."""
    return 200, ArtifactSelector.get_delta(
        request=request, artifact_id=artifact_id, since_version=since_version
    )


@router.get("/{artifact_id}/diff", response={200: dict, 404: dict})
def get_version_diff(request, artifact_id: UUID, from_version: int = 1, to_version: int = 2):
    """Retrieve text diff patch between any two historical versions."""
    return 200, ArtifactSelector.get_version_diff(
        request=request, artifact_id=artifact_id, from_version=from_version, to_version=to_version
    )


@router.post("/{artifact_id}/revert", response={201: ArtifactResponseSchema, 400: dict, 403: dict, 404: dict})
def revert_artifact(request, artifact_id: UUID, target_version_number: int, commit_message: str = ""):
    """Revert an artifact to a historical version snapshot."""
    artifact = ArtifactService.revert_artifact(
        request=request,
        artifact_id=artifact_id,
        target_version_number=target_version_number,
        commit_message=commit_message,
    )
    return 201, ArtifactResponseSchema.from_model(artifact)


@router.get("/{artifact_id}/download")
def download_document(request, artifact_id: UUID):
    """Download the raw document file."""
    artifact = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=artifact_id)
    if artifact.type != "document" or not hasattr(artifact, "document"):
        raise HttpError(404, "Artifact is not a document file.")
    doc = artifact.document
    response = FileResponse(doc.file.open("rb"))
    response["Content-Disposition"] = f'attachment; filename="{artifact.title}"'
    return response


# ---------------------------------------------------------------------------
# In-Place Working Drafts
# ---------------------------------------------------------------------------


@router.get("/{artifact_id}/draft", response={200: ArtifactDraftResponseSchema, 404: dict})
def get_draft(request, artifact_id: UUID):
    """Retrieve active in-place draft for an artifact."""
    draft = DraftSelector.get_draft(request=request, artifact_id=artifact_id)
    if not draft:
        return 404, {"message": "No active draft exists for this artifact."}
    return 200, ArtifactDraftResponseSchema.from_model(draft)


@router.patch("/{artifact_id}/draft", response={200: ArtifactDraftResponseSchema, 400: dict, 404: dict})
def patch_draft(request, artifact_id: UUID, data: DraftPatchSchema):
    """Apply mutation operations to an artifact working draft."""
    draft = DraftService.patch_draft(
        request=request, artifact_id=artifact_id, operations=data.operations
    )
    return 200, ArtifactDraftResponseSchema.from_model(draft)


@router.delete("/{artifact_id}/draft", response={200: dict, 404: dict})
def discard_draft(request, artifact_id: UUID):
    """Discard the working draft for an artifact."""
    DraftService.discard_draft(request=request, artifact_id=artifact_id)
    return 200, {"message": "Draft discarded."}


@router.post("/{artifact_id}/commit", response={200: ArtifactResponseSchema, 400: dict, 404: dict})
def commit_draft(request, artifact_id: UUID, data: ArtifactCommitSchema):
    """Commit working draft as a new immutable version snapshot."""
    artifact = DraftService.commit_draft(
        request=request, artifact_id=artifact_id, commit_message=data.commit_message
    )
    return 200, ArtifactResponseSchema.from_model(artifact)


# ---------------------------------------------------------------------------
# Graph & Comments
# ---------------------------------------------------------------------------


@router.post("/relationships", response={201: RelationshipResponseSchema, 400: dict, 403: dict})
def create_relationship(request, data: RelationshipCreateSchema):
    """Create a directed relationship edge between two artifacts."""
    rel = RelationshipService.create_relationship(request=request, data=data)
    return 201, RelationshipResponseSchema.from_model(rel)


@router.get("/{artifact_id}/relationships", response=dict)
def get_relationships(request, artifact_id: UUID):
    """Retrieve incoming and outgoing relationship edges for an artifact."""
    rels = ArtifactSelector.get_relationships(request=request, artifact_id=artifact_id)
    return {
        "outgoing": [RelationshipResponseSchema.from_model(r) for r in rels["outgoing"]],
        "incoming": [RelationshipResponseSchema.from_model(r) for r in rels["incoming"]],
    }


@router.get("/{artifact_id}/comments", response=list[CommentResponseSchema])
def list_comments(request, artifact_id: UUID):
    """List discussion comments for an artifact."""
    comments = ArtifactSelector.list_comments(request=request, artifact_id=artifact_id)
    return [CommentResponseSchema.from_model(c) for c in comments]


@router.post("/{artifact_id}/comments", response={201: CommentResponseSchema, 400: dict})
def post_comment(request, artifact_id: UUID, body: str):
    """Post a comment on an artifact."""
    comment = CommentService.post_comment(request=request, artifact_id=artifact_id, body=body)
    return 201, CommentResponseSchema.from_model(comment)
