from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field
from typing import Any, List, Optional


class ArtifactCreateSchema(BaseModel):
    type: str  # document, skill, decision, memory
    title: str
    collection_id: Optional[UUID] = None
    lifecycle_state: Optional[str] = "draft"
    
    # Subtype fields (optional, used based on type)
    skill_md_content: Optional[str] = None
    decision_text: Optional[str] = None
    rationale: Optional[str] = None
    memory_content: Optional[str] = None
    memory_scope: Optional[str] = None


class ArtifactUpdateSchema(BaseModel):
    title: Optional[str] = None
    lifecycle_state: Optional[str] = None
    expected_version_number: Optional[int] = None  # OCC check
    
    # Subtype updates
    skill_md_content: Optional[str] = None
    decision_text: Optional[str] = None
    rationale: Optional[str] = None
    decision_status: Optional[str] = None
    memory_content: Optional[str] = None
    memory_scope: Optional[str] = None


class ArtifactRevertSchema(BaseModel):
    target_version_number: int
    commit_message: Optional[str] = None


class ArtifactResponseSchema(BaseModel):
    id: UUID
    type: str
    title: str
    owner_id: UUID
    owner_display_name: str
    created_by_id: UUID
    created_by_display_name: str
    collection_id: Optional[UUID] = None
    lifecycle_state: str
    embedding_status: str
    locked_by_id: Optional[UUID] = None
    has_draft: bool = False
    draft_updated_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    
    # Subtype-specific content
    details: Optional[dict] = None

    class Config:
        from_attributes = True

    @classmethod
    def from_model(cls, artifact_obj):
        # Resolve details dictionary based on type
        details = {}
        if artifact_obj.type == "document" and hasattr(artifact_obj, "document"):
            doc = artifact_obj.document
            details = {
                "file_url": doc.file.url if doc.file else None,
                "format": doc.format,
                "size": doc.file.size if doc.file else 0,
            }
        elif artifact_obj.type == "skill" and hasattr(artifact_obj, "skill"):
            details = {
                "skill_md_content": artifact_obj.skill.skill_md_content,
                "usage_count": artifact_obj.skill.usage_count,
            }
        elif artifact_obj.type == "decision" and hasattr(artifact_obj, "decision"):
            details = {
                "decision_text": artifact_obj.decision.decision_text,
                "rationale": artifact_obj.decision.rationale,
                "status": artifact_obj.decision.status,
            }
        elif artifact_obj.type == "memory" and hasattr(artifact_obj, "memory"):
            details = {
                "content": artifact_obj.memory.content,
                "scope": artifact_obj.memory.scope,
                "expires_at": artifact_obj.memory.expires_at.isoformat() if artifact_obj.memory.expires_at else None,
            }

        # Check draft presence
        has_draft = False
        draft_updated_at = None
        if hasattr(artifact_obj, "draft") and artifact_obj.draft:
            has_draft = True
            draft_updated_at = artifact_obj.draft.updated_at

        return cls(
            id=artifact_obj.id,
            type=artifact_obj.type,
            title=artifact_obj.title,
            owner_id=artifact_obj.owner_id,
            owner_display_name=str(artifact_obj.owner),
            created_by_id=artifact_obj.created_by_id,
            created_by_display_name=str(artifact_obj.created_by),
            collection_id=artifact_obj.collection_id,
            lifecycle_state=artifact_obj.lifecycle_state,
            embedding_status=artifact_obj.embedding_status,
            locked_by_id=artifact_obj.locked_by_id,
            has_draft=has_draft,
            draft_updated_at=draft_updated_at,
            created_at=artifact_obj.created_at,
            updated_at=artifact_obj.updated_at,
            details=details,
        )


class ArtifactVersionResponseSchema(BaseModel):
    id: UUID
    artifact_id: UUID
    version_number: int
    file_url: str
    diff_content: str
    created_by_id: Optional[UUID] = None
    created_by_display_name: Optional[str] = None
    commit_message: str
    created_at: datetime

    class Config:
        from_attributes = True

    @classmethod
    def from_model(cls, version_obj):
        return cls(
            id=version_obj.id,
            artifact_id=version_obj.artifact_id,
            version_number=version_obj.version_number,
            file_url=version_obj.file_instance.url if version_obj.file_instance else "",
            diff_content=version_obj.diff_content,
            created_by_id=version_obj.created_by_id,
            created_by_display_name=str(version_obj.created_by) if version_obj.created_by else None,
            commit_message=version_obj.commit_message or "",
            created_at=version_obj.created_at,
        )


class RelationshipCreateSchema(BaseModel):
    from_artifact_id: UUID
    to_artifact_id: UUID
    relation_type: str


class RelationshipResponseSchema(BaseModel):
    id: UUID
    from_artifact_id: UUID
    from_artifact_title: str
    to_artifact_id: UUID
    to_artifact_title: str
    relation_type: str
    created_at: datetime
    created_by_id: UUID
    created_by_display_name: str

    class Config:
        from_attributes = True

    @classmethod
    def from_model(cls, rel_obj):
        return cls(
            id=rel_obj.id,
            from_artifact_id=rel_obj.from_artifact_id,
            from_artifact_title=rel_obj.from_artifact.title,
            to_artifact_id=rel_obj.to_artifact_id,
            to_artifact_title=rel_obj.to_artifact.title,
            relation_type=rel_obj.relation_type,
            created_at=rel_obj.created_at,
            created_by_id=rel_obj.created_by_id,
            created_by_display_name=str(rel_obj.created_by),
        )


class CommentResponseSchema(BaseModel):
    id: UUID
    artifact_id: UUID
    author_id: UUID
    author_display_name: str
    body: str
    created_at: datetime

    class Config:
        from_attributes = True

    @classmethod
    def from_model(cls, comment_obj):
        return cls(
            id=comment_obj.id,
            artifact_id=comment_obj.artifact_id,
            author_id=comment_obj.author_id,
            author_display_name=str(comment_obj.author),
            body=comment_obj.body,
            created_at=comment_obj.created_at,
        )


# --- Draft Schemas ---

class DraftOperationSchema(BaseModel):
    """A single mutation operation on an artifact working draft."""
    op: str  # replace_block, insert_block, delete_block, move_block, replace_text
    block_id: Optional[str] = None
    block_type: Optional[str] = None
    content: Any = None
    attrs: Optional[dict] = None
    position_index: Optional[int] = None
    after_block_id: Optional[str] = None


class DraftPatchSchema(BaseModel):
    operations: List[DraftOperationSchema]


class ArtifactCommitSchema(BaseModel):
    commit_message: str = ""


class ArtifactDraftResponseSchema(BaseModel):
    id: UUID
    artifact_id: UUID
    block_data: Any
    updated_at: datetime
    last_edited_by_id: Optional[UUID] = None
    last_edited_by_display_name: Optional[str] = None
    participant_names: List[str] = Field(default_factory=list)
    commit_msg_hint: str = ""

    class Config:
        from_attributes = True

    @classmethod
    def from_model(cls, draft_obj):
        return cls(
            id=draft_obj.id,
            artifact_id=draft_obj.artifact_id,
            block_data=draft_obj.block_data,
            updated_at=draft_obj.updated_at,
            last_edited_by_id=draft_obj.last_edited_by_id,
            last_edited_by_display_name=str(draft_obj.last_edited_by) if draft_obj.last_edited_by else None,
            participant_names=[str(p) for p in draft_obj.participants.all()],
            commit_msg_hint=draft_obj.commit_msg_hint,
        )
