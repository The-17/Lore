import json
from uuid import UUID, uuid4
from typing import Any, Optional

from django.core.files.base import ContentFile
from django.db import models, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import UploadedFile
from ninja.errors import HttpError

from apps.collections.models import Collection
from apps.common.realtime import publish_artifact_event
from apps.common.security import (
    get_allowed_collections_for_request,
    scope_artifacts_queryset,
    scope_collections_queryset,
)

from .chunking import chunk_text_content
from .models import (
    Artifact,
    ArtifactComment,
    ArtifactDraft,
    ArtifactRelationship,
    ArtifactVersion,
    DecisionArtifact,
    DocumentArtifact,
    LifecycleState,
    MemoryArtifact,
    SkillArtifact,
)
from .schemas import ArtifactCreateSchema, ArtifactUpdateSchema, RelationshipCreateSchema
from .selectors import ArtifactSelector, DraftSelector
from .utils import compute_diff
from .wiki_links import extract_and_sync_wiki_links


def _initial_lifecycle_state(requested: str, request) -> str:
    """Clamp initial lifecycle state according to agent capability."""
    agent_token = getattr(request, "agent_token", None)
    if agent_token is None or getattr(agent_token, "can_auto_approve", False):
        valid_states = {
            LifecycleState.DRAFT,
            LifecycleState.REVIEW,
            LifecycleState.APPROVED,
            LifecycleState.PUBLISHED,
            LifecycleState.ARCHIVED,
        }
        if requested in valid_states:
            return requested
        return LifecycleState.DRAFT

    if requested in {LifecycleState.DRAFT, LifecycleState.REVIEW}:
        return requested
    return LifecycleState.DRAFT


def _require_human_reviewer(request) -> None:
    """Ensure autonomous agent tokens cannot self-approve or self-reject."""
    if getattr(request, "agent_token", None) is not None:
        raise HttpError(403, "Only human reviewers can approve or reject artifacts.")


def _serialize_inline_spans(spans: list) -> str:
    """Serialize inline span elements to markdown."""
    result = ""
    for span in spans:
        if isinstance(span, str):
            result += span
            continue
        span_type = span.get("type", "text")
        text = span.get("text", "")
        children = span.get("children", [])
        inner = text if text else _serialize_inline_spans(children)

        if span_type == "text":
            result += inner
        elif span_type == "bold":
            result += f"**{inner}**"
        elif span_type == "italic":
            result += f"*{inner}*"
        elif span_type == "code":
            result += f"`{inner}`"
        elif span_type == "strikethrough":
            result += f"~~{inner}~~"
        elif span_type == "link":
            href = span.get("attrs", {}).get("href", "")
            result += f"[{inner}]({href})"
        elif span_type == "wikilink":
            title = span.get("attrs", {}).get("title", inner)
            result += f"[[{title}]]"
        elif span_type == "highlight":
            result += f"=={inner}=="
        else:
            result += inner
    return result


def _serialize_blocks_to_markdown(block_data: list) -> str:
    """Convert ADM block AST to markdown."""
    lines = []
    for block in block_data:
        block_type = block.get("type", "paragraph")
        content = block.get("content", "")
        attrs = block.get("attrs", {})

        if isinstance(content, list):
            content = _serialize_inline_spans(content)

        if block_type == "heading":
            level = attrs.get("level", 1)
            lines.append(f"{'#' * level} {content}")
            lines.append("")
        elif block_type == "paragraph":
            lines.append(content)
            lines.append("")
        elif block_type == "code":
            lang = attrs.get("language", "")
            lines.append(f"```{lang}")
            lines.append(content)
            lines.append("```")
            lines.append("")
        elif block_type == "blockquote":
            for line in content.split("\n"):
                lines.append(f"> {line}")
            lines.append("")
        elif block_type == "list":
            children = block.get("children", [])
            for child in children:
                child_content = child.get("content", "")
                if isinstance(child_content, list):
                    child_content = _serialize_inline_spans(child_content)
                style = attrs.get("listStyle", "bullet")
                if child.get("type") == "task_item":
                    checked = child.get("attrs", {}).get("checked", False)
                    lines.append(f"- [{'x' if checked else ' '}] {child_content}")
                elif style == "ordered":
                    lines.append(f"1. {child_content}")
                else:
                    lines.append(f"- {child_content}")
            lines.append("")
        elif block_type == "thematic_break":
            lines.append("---")
            lines.append("")
        elif block_type == "table":
            if isinstance(content, list) and len(content) > 0:
                for i, row in enumerate(content):
                    cells = [str(c) for c in row] if isinstance(row, list) else [str(row)]
                    lines.append("| " + " | ".join(cells) + " |")
                    if i == 0:
                        lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
                lines.append("")
        elif block_type == "mermaid":
            lines.append("```mermaid")
            lines.append(content)
            lines.append("```")
            lines.append("")
        elif block_type == "callout":
            callout_type = attrs.get("calloutType", "NOTE")
            lines.append(f"> [!{callout_type}]")
            for line in content.split("\n"):
                lines.append(f"> {line}")
            lines.append("")
        else:
            lines.append(str(content))
            lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def get_artifact_text_content(artifact: Artifact) -> str:
    """Retrieve raw text content for any artifact subtype."""
    if artifact.type == "document" and hasattr(artifact, "document") and artifact.document.file:
        try:
            artifact.document.file.seek(0)
            return artifact.document.file.read().decode("utf-8", errors="replace")
        except Exception:
            return ""
    elif artifact.type == "skill" and hasattr(artifact, "skill"):
        return artifact.skill.skill_md_content or ""
    elif artifact.type == "decision" and hasattr(artifact, "decision"):
        return artifact.decision.decision_text or ""
    elif artifact.type == "memory" and hasattr(artifact, "memory"):
        return artifact.memory.content or ""
    elif artifact.current_version and artifact.current_version.file_instance:
        try:
            artifact.current_version.file_instance.seek(0)
            return artifact.current_version.file_instance.read().decode("utf-8", errors="replace")
        except Exception:
            return ""
    return ""


def get_next_version_number(artifact: Artifact) -> int:
    """Calculate next sequential version number with row lock."""
    with transaction.atomic():
        Artifact.objects.select_for_update().get(id=artifact.id)
        max_ver = artifact.versions.aggregate(max_num=models.Max("version_number"))["max_num"]
        return (max_ver or 0) + 1


def create_initial_version(artifact: Artifact, content_text: str, created_by: Any) -> ArtifactVersion:
    """Create version 1 for a newly created artifact."""
    content_bytes = content_text.encode("utf-8")
    version = ArtifactVersion.objects.create(
        artifact=artifact,
        version_number=1,
        file_instance=ContentFile(content_bytes, name=f"v1_{artifact.title}.md"),
        diff_content="",
        created_by=created_by,
        commit_message="Initial creation",
    )
    artifact.current_version = version
    artifact.save(update_fields=["current_version"])

    if content_text:
        try:
            extract_and_sync_wiki_links(artifact, content_text, created_by)
            chunk_text_content(artifact, version, content_text)
        except Exception:
            pass

    return version


def update_artifact_version(
    artifact: Artifact,
    new_content_text: str,
    created_by: Any,
    commit_message: str = "",
) -> ArtifactVersion:
    """Create a new immutable ArtifactVersion with diffing and chunk sync."""
    old_content_text = get_artifact_text_content(artifact)
    version_num = get_next_version_number(artifact)

    old_bytes = old_content_text.encode("utf-8")
    new_bytes = new_content_text.encode("utf-8")
    diff_patch = compute_diff(old_bytes, new_bytes)

    version = ArtifactVersion.objects.create(
        artifact=artifact,
        version_number=version_num,
        file_instance=ContentFile(new_bytes, name=f"v{version_num}_{artifact.title}.md"),
        diff_content=diff_patch,
        created_by=created_by,
        commit_message=commit_message or f"Update to version {version_num}",
    )

    if artifact.type == "skill" and hasattr(artifact, "skill"):
        artifact.skill.skill_md_content = new_content_text
        artifact.skill.save(update_fields=["skill_md_content"])
    elif artifact.type == "decision" and hasattr(artifact, "decision"):
        artifact.decision.decision_text = new_content_text
        artifact.decision.save(update_fields=["decision_text"])
    elif artifact.type == "memory" and hasattr(artifact, "memory"):
        artifact.memory.content = new_content_text
        artifact.memory.save(update_fields=["content"])
    elif artifact.type == "document" and hasattr(artifact, "document"):
        doc = artifact.document
        doc.file.save(artifact.title, ContentFile(new_bytes), save=False)
        doc.save()

    artifact.current_version = version
    artifact.save(update_fields=["current_version", "updated_at"])

    try:
        extract_and_sync_wiki_links(artifact, new_content_text, created_by)
        chunk_text_content(artifact, version, new_content_text)
    except Exception:
        pass

    return version


def revert_artifact_to_version(
    artifact: Artifact,
    target_version: ArtifactVersion,
    created_by: Any,
    commit_message: str = "",
) -> ArtifactVersion:
    """Revert an artifact by appending a new version with historical content."""
    current_content_text = get_artifact_text_content(artifact)

    if target_version.file_instance:
        target_version.file_instance.seek(0)
        target_content_bytes = target_version.file_instance.read()
        target_content_text = target_content_bytes.decode("utf-8", errors="replace")
    else:
        target_content_text = ""
        target_content_bytes = b""

    version_num = get_next_version_number(artifact)
    diff_patch = compute_diff(current_content_text.encode("utf-8"), target_content_bytes)

    new_version = ArtifactVersion.objects.create(
        artifact=artifact,
        version_number=version_num,
        file_instance=ContentFile(
            target_content_bytes,
            name=f"v{version_num}_revert_to_v{target_version.version_number}_{artifact.title}.md",
        ),
        diff_content=diff_patch,
        created_by=created_by,
        commit_message=commit_message or f"Reverted artifact to version {target_version.version_number}",
    )

    if artifact.type == "skill" and hasattr(artifact, "skill"):
        artifact.skill.skill_md_content = target_content_text
        artifact.skill.save(update_fields=["skill_md_content"])
    elif artifact.type == "decision" and hasattr(artifact, "decision"):
        artifact.decision.decision_text = target_content_text
        artifact.decision.save(update_fields=["decision_text"])
    elif artifact.type == "memory" and hasattr(artifact, "memory"):
        artifact.memory.content = target_content_text
        artifact.memory.save(update_fields=["content"])
    elif artifact.type == "document" and hasattr(artifact, "document"):
        doc = artifact.document
        doc.file.save(artifact.title, ContentFile(target_content_bytes), save=False)
        doc.save()

    artifact.current_version = new_version
    artifact.save(update_fields=["current_version", "updated_at"])

    try:
        extract_and_sync_wiki_links(artifact, target_content_text, created_by)
        chunk_text_content(artifact, new_version, target_content_text)
    except Exception:
        pass

    return new_version


class ArtifactService:
    """Mutations for artifacts."""

    @staticmethod
    def create_text_artifact(*, request, data: ArtifactCreateSchema) -> Artifact:
        principal = getattr(request, "principal", None)
        agent_token = getattr(request, "agent_token", None)
        if not principal:
            raise HttpError(400, "No principal found for request context.")

        if data.type == "document":
            raise HttpError(400, "Documents must be created via /api/artifacts/upload")

        with transaction.atomic():
            collection = None
            if data.collection_id:
                allowed_cols = get_allowed_collections_for_request(request)
                allowed_ids = [c.id for c in allowed_cols]
                if agent_token and agent_token.restricted_collection and data.collection_id not in allowed_ids:
                    raise HttpError(403, "Access denied: Collection is outside sandboxed scope.")
                collection = get_object_or_404(Collection, id=data.collection_id)
            elif agent_token and agent_token.restricted_collection:
                raise HttpError(403, "Scope-restricted agents cannot create root-level artifacts.")

            artifact = Artifact.objects.create(
                type=data.type,
                title=data.title,
                owner=principal,
                created_by=principal,
                collection=collection,
                inherit_permissions=(collection is not None),
                lifecycle_state=_initial_lifecycle_state(data.lifecycle_state or "draft", request),
            )

            text_to_scan = ""
            if data.type == "skill":
                text_to_scan = data.skill_md_content or ""
                SkillArtifact.objects.create(artifact=artifact, skill_md_content=text_to_scan)
            elif data.type == "decision":
                text_to_scan = (data.decision_text or "") + "\n" + (data.rationale or "")
                DecisionArtifact.objects.create(
                    artifact=artifact,
                    decision_text=data.decision_text or "",
                    rationale=data.rationale or "",
                )
            elif data.type == "memory":
                text_to_scan = data.memory_content or ""
                MemoryArtifact.objects.create(
                    artifact=artifact, content=text_to_scan, scope=data.memory_scope or ""
                )

            create_initial_version(artifact, text_to_scan, principal)

            publish_artifact_event(
                event_type="artifact.created",
                artifact_id=str(artifact.id),
                payload={"title": artifact.title, "type": artifact.type, "state": artifact.lifecycle_state},
                owner_id=str(artifact.owner_id),
            )
            return artifact

    @staticmethod
    def upload_document(
        *,
        request,
        file: UploadedFile,
        collection_id: Optional[UUID] = None,
        lifecycle_state: str = "draft",
    ) -> Artifact:
        principal = getattr(request, "principal", None)
        agent_token = getattr(request, "agent_token", None)
        if not principal:
            raise HttpError(400, "No principal found for request context.")

        with transaction.atomic():
            collection = None
            if collection_id:
                allowed_cols = get_allowed_collections_for_request(request)
                allowed_ids = [c.id for c in allowed_cols]
                if agent_token and agent_token.restricted_collection and collection_id not in allowed_ids:
                    raise HttpError(403, "Access denied: Collection is outside sandboxed scope.")
                collection = get_object_or_404(Collection, id=collection_id)
            elif agent_token and agent_token.restricted_collection:
                raise HttpError(403, "Scope-restricted agents cannot create root-level artifacts.")

            new_content = file.read()
            file.seek(0)

            existing_artifact = Artifact.objects.filter(
                type="document",
                title=file.name,
                collection=collection,
                deleted_at__isnull=True,
            ).first()

            if existing_artifact:
                if existing_artifact.locked_by and existing_artifact.locked_by != principal:
                    raise HttpError(403, f"Artifact is locked by {existing_artifact.locked_by}")

                doc = existing_artifact.document
                old_content = doc.file.read()
                doc.file.seek(0)

                version_num = existing_artifact.versions.count() + 1
                diff_patch = compute_diff(old_content, new_content)
                version = ArtifactVersion.objects.create(
                    artifact=existing_artifact,
                    version_number=version_num,
                    file_instance=ContentFile(old_content, name=f"v{version_num}_{file.name}"),
                    diff_content=diff_patch,
                    created_by=principal,
                    commit_message=f"Update document to version {version_num}",
                )

                existing_artifact.current_version = version
                existing_artifact.lifecycle_state = _initial_lifecycle_state(lifecycle_state, request)
                existing_artifact.save()

                doc.file.save(file.name, file, save=False)
                doc.save()

                try:
                    text_str = new_content.decode("utf-8")
                    extract_and_sync_wiki_links(existing_artifact, text_str, principal)
                    chunk_text_content(existing_artifact, version, text_str)
                except Exception:
                    pass

                publish_artifact_event(
                    event_type="artifact.updated",
                    artifact_id=str(existing_artifact.id),
                    payload={"version_number": version_num, "diff": diff_patch, "state": existing_artifact.lifecycle_state},
                    owner_id=str(existing_artifact.owner_id),
                )
                return existing_artifact

            artifact = Artifact.objects.create(
                type="document",
                title=file.name,
                owner=principal,
                created_by=principal,
                collection=collection,
                inherit_permissions=(collection is not None),
                lifecycle_state=_initial_lifecycle_state(lifecycle_state, request),
            )

            doc = DocumentArtifact.objects.create(
                artifact=artifact,
                file=file,
                format=file.name.split(".")[-1] if "." in file.name else "markdown",
            )

            version = ArtifactVersion.objects.create(
                artifact=artifact,
                version_number=1,
                file_instance=ContentFile(new_content, name=f"v1_{file.name}"),
                diff_content="",
                created_by=principal,
                commit_message="Initial document upload",
            )
            artifact.current_version = version
            artifact.save(update_fields=["current_version"])

            try:
                text_str = new_content.decode("utf-8")
                extract_and_sync_wiki_links(artifact, text_str, principal)
                chunk_text_content(artifact, version, text_str)
            except Exception:
                pass

            publish_artifact_event(
                event_type="artifact.created",
                artifact_id=str(artifact.id),
                payload={"title": artifact.title, "type": "document", "state": artifact.lifecycle_state},
                owner_id=str(artifact.owner_id),
            )
            return artifact

    @staticmethod
    def update_artifact(*, request, artifact_id: UUID, data: ArtifactUpdateSchema) -> Artifact:
        artifact = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=artifact_id)
        principal = getattr(request, "principal", None)

        if artifact.locked_by and artifact.locked_by != principal:
            raise HttpError(403, f"Artifact is locked by {artifact.locked_by}")

        current_ver = artifact.current_version.version_number if artifact.current_version else 1
        if data.expected_version_number is not None and data.expected_version_number != current_ver:
            raise HttpError(
                409,
                f"Artifact version mismatch: current version is {current_ver}, expected {data.expected_version_number}",
            )

        with transaction.atomic():
            if data.title:
                artifact.title = data.title
            if data.lifecycle_state:
                artifact.lifecycle_state = data.lifecycle_state
            artifact.save()

            new_text_content = None
            if artifact.type == "skill" and hasattr(artifact, "skill"):
                if data.skill_md_content is not None:
                    new_text_content = data.skill_md_content
            elif artifact.type == "decision" and hasattr(artifact, "decision"):
                decision = artifact.decision
                if data.decision_text is not None:
                    new_text_content = data.decision_text
                if data.rationale is not None:
                    decision.rationale = data.rationale
                if data.decision_status is not None:
                    decision.status = data.decision_status
                decision.save()
            elif artifact.type == "memory" and hasattr(artifact, "memory"):
                mem = artifact.memory
                if data.memory_content is not None:
                    new_text_content = data.memory_content
                if data.memory_scope is not None:
                    mem.scope = data.memory_scope
                mem.save()

            if new_text_content is not None:
                version = update_artifact_version(
                    artifact, new_text_content, principal, commit_message="Updated content"
                )
                version_num = version.version_number
            else:
                version_num = current_ver

            publish_artifact_event(
                event_type="artifact.updated",
                artifact_id=str(artifact.id),
                payload={"title": artifact.title, "state": artifact.lifecycle_state, "version_number": version_num},
                owner_id=str(artifact.owner_id),
            )
            return artifact

    @staticmethod
    def delete_artifact(*, request, artifact_id: UUID) -> None:
        artifact = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=artifact_id)
        principal = getattr(request, "principal", None)

        if artifact.locked_by and artifact.locked_by != principal:
            raise HttpError(403, f"Artifact is locked by {artifact.locked_by}")

        with transaction.atomic():
            artifact.deleted_at = timezone.now()
            artifact.save(update_fields=["deleted_at", "updated_at"])

    @staticmethod
    def lock_artifact(*, request, artifact_id: UUID) -> Artifact:
        artifact = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=artifact_id)
        principal = getattr(request, "principal", None)
        if not principal:
            raise HttpError(400, "No principal found.")
        if artifact.locked_by:
            raise HttpError(400, f"Artifact is already locked by {artifact.locked_by}")

        artifact.locked_by = principal
        artifact.save(update_fields=["locked_by", "updated_at"])
        return artifact

    @staticmethod
    def unlock_artifact(*, request, artifact_id: UUID) -> Artifact:
        artifact = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=artifact_id)
        principal = getattr(request, "principal", None)
        if not principal:
            raise HttpError(400, "No principal found.")
        if not artifact.locked_by:
            raise HttpError(400, "Artifact is not locked.")
        if artifact.locked_by != principal and artifact.owner != principal:
            raise HttpError(403, "You do not have permission to unlock this artifact.")

        artifact.locked_by = None
        artifact.save(update_fields=["locked_by", "updated_at"])
        return artifact

    @staticmethod
    def approve_artifact(*, request, artifact_id: UUID) -> Artifact:
        _require_human_reviewer(request)
        artifact = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=artifact_id)

        with transaction.atomic():
            previous_state = artifact.lifecycle_state
            artifact.lifecycle_state = LifecycleState.APPROVED
            artifact.save(update_fields=["lifecycle_state", "updated_at"])

            publish_artifact_event(
                event_type="artifact.state_changed",
                artifact_id=str(artifact.id),
                payload={"new_state": artifact.lifecycle_state, "previous_state": previous_state, "actor": str(request.user)},
                owner_id=str(artifact.owner_id),
            )
            return artifact

    @staticmethod
    def reject_artifact(*, request, artifact_id: UUID) -> Artifact:
        _require_human_reviewer(request)
        artifact = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=artifact_id)

        with transaction.atomic():
            previous_state = artifact.lifecycle_state
            artifact.lifecycle_state = LifecycleState.REJECTED
            artifact.save(update_fields=["lifecycle_state", "updated_at"])

            publish_artifact_event(
                event_type="artifact.state_changed",
                artifact_id=str(artifact.id),
                payload={"new_state": artifact.lifecycle_state, "previous_state": previous_state, "actor": str(request.user)},
                owner_id=str(artifact.owner_id),
            )
            return artifact

    @staticmethod
    def revert_artifact(
        *, request, artifact_id: UUID, target_version_number: int, commit_message: str = ""
    ) -> Artifact:
        artifact = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=artifact_id)
        principal = getattr(request, "principal", None)

        if artifact.locked_by and artifact.locked_by != principal:
            raise HttpError(403, f"Artifact is locked by {artifact.locked_by}")

        target_version = artifact.versions.filter(version_number=target_version_number).first()
        if not target_version:
            raise HttpError(404, f"Version {target_version_number} not found for this artifact.")

        with transaction.atomic():
            new_version = revert_artifact_to_version(
                artifact=artifact,
                target_version=target_version,
                created_by=principal,
                commit_message=commit_message,
            )
            publish_artifact_event(
                event_type="artifact.updated",
                artifact_id=str(artifact.id),
                payload={"version_number": new_version.version_number, "state": artifact.lifecycle_state},
                owner_id=str(artifact.owner_id),
            )
            return artifact


class DraftService:
    """Mutations for in-place working drafts."""

    @staticmethod
    def patch_draft(*, request, artifact_id: UUID, operations: list) -> ArtifactDraft:
        principal = getattr(request, "principal", None)
        if not principal:
            raise HttpError(400, "No principal found for request context.")

        artifact = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=artifact_id)

        with transaction.atomic():
            draft, created = ArtifactDraft.objects.get_or_create(
                artifact=artifact,
                defaults={"block_data": [], "last_edited_by": principal},
            )

            blocks = list(draft.block_data) if draft.block_data else []

            for op in operations:
                op_type = op.op if hasattr(op, "op") else op.get("op")
                block_id = op.block_id if hasattr(op, "block_id") else op.get("block_id")
                content = op.content if hasattr(op, "content") else op.get("content")
                attrs = op.attrs if hasattr(op, "attrs") else op.get("attrs")
                btype = op.block_type if hasattr(op, "block_type") else op.get("block_type")
                after_id = op.after_block_id if hasattr(op, "after_block_id") else op.get("after_block_id")
                pos_idx = op.position_index if hasattr(op, "position_index") else op.get("position_index")

                if op_type == "replace_block":
                    for i, b in enumerate(blocks):
                        if b.get("id") == block_id:
                            if content is not None:
                                blocks[i]["content"] = content
                            if attrs is not None:
                                blocks[i]["attrs"] = attrs
                            if btype is not None:
                                blocks[i]["type"] = btype
                            break
                elif op_type == "insert_block":
                    new_block = {
                        "id": block_id or f"blk_{uuid4().hex[:12]}",
                        "type": btype or "paragraph",
                        "content": content or [],
                        "attrs": attrs or {},
                    }
                    if after_id:
                        idx = next((i for i, b in enumerate(blocks) if b.get("id") == after_id), len(blocks) - 1)
                        blocks.insert(idx + 1, new_block)
                    elif pos_idx is not None:
                        blocks.insert(pos_idx, new_block)
                    else:
                        blocks.append(new_block)
                elif op_type == "delete_block":
                    blocks = [b for b in blocks if b.get("id") != block_id]
                elif op_type == "move_block":
                    block_to_move = None
                    for i, b in enumerate(blocks):
                        if b.get("id") == block_id:
                            block_to_move = blocks.pop(i)
                            break
                    if block_to_move:
                        if after_id:
                            idx = next((i for i, b in enumerate(blocks) if b.get("id") == after_id), len(blocks) - 1)
                            blocks.insert(idx + 1, block_to_move)
                        elif pos_idx is not None:
                            blocks.insert(pos_idx, block_to_move)
                        else:
                            blocks.append(block_to_move)

            draft.block_data = blocks
            draft.last_edited_by = principal
            draft.save(update_fields=["block_data", "last_edited_by", "updated_at"])
            draft.participants.add(principal)

            publish_artifact_event(
                event_type="draft.updated",
                artifact_id=str(artifact.id),
                payload={"draft_id": str(draft.id), "editor": str(principal)},
                owner_id=str(artifact.owner_id),
            )
            return draft

    @staticmethod
    def discard_draft(*, request, artifact_id: UUID) -> None:
        artifact = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=artifact_id)
        with transaction.atomic():
            deleted_count, _ = ArtifactDraft.objects.filter(artifact=artifact).delete()
            if deleted_count == 0:
                raise HttpError(404, "No active draft to discard.")

    @staticmethod
    def commit_draft(*, request, artifact_id: UUID, commit_message: str = "") -> Artifact:
        principal = getattr(request, "principal", None)
        if not principal:
            raise HttpError(400, "No principal found for request context.")

        artifact = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=artifact_id)

        with transaction.atomic():
            draft = ArtifactDraft.objects.filter(artifact=artifact).first()
            if not draft:
                raise HttpError(404, "No active draft to commit.")

            new_content = _serialize_blocks_to_markdown(draft.block_data)
            current_content = get_artifact_text_content(artifact)

            if new_content.strip() == current_content.strip():
                raise HttpError(400, "Draft content is identical to the current version. Nothing to commit.")

            version = update_artifact_version(
                artifact=artifact,
                new_content_text=new_content,
                created_by=principal,
                commit_message=commit_message or draft.commit_msg_hint or "Draft committed",
            )

            draft.delete()

            publish_artifact_event(
                event_type="artifact.committed",
                artifact_id=str(artifact.id),
                payload={"version_number": version.version_number, "commit_message": version.commit_message},
                owner_id=str(artifact.owner_id),
            )
            return artifact


class RelationshipService:
    """Mutations for graph edges."""

    @staticmethod
    def create_relationship(*, request, data: RelationshipCreateSchema) -> ArtifactRelationship:
        principal = getattr(request, "principal", None)
        if not principal:
            raise HttpError(400, "No principal found.")

        with transaction.atomic():
            from_art = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=data.from_artifact_id)
            to_art = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=data.to_artifact_id)

            rel = ArtifactRelationship.objects.create(
                from_artifact=from_art,
                to_artifact=to_art,
                relation_type=data.relation_type,
                created_by=principal,
            )

            publish_artifact_event(
                event_type="artifact.relationship_created",
                artifact_id=str(from_art.id),
                payload={"to_artifact_id": str(to_art.id), "relation_type": data.relation_type},
            )
            return rel


class CommentService:
    """Mutations for artifact comments."""

    @staticmethod
    def post_comment(*, request, artifact_id: UUID, body: str) -> ArtifactComment:
        principal = getattr(request, "principal", None)
        if not principal:
            raise HttpError(400, "No principal found.")

        with transaction.atomic():
            artifact = ArtifactSelector.get_by_id_for_request(request=request, artifact_id=artifact_id)
            return ArtifactComment.objects.create(
                artifact=artifact,
                author=principal,
                body=body,
            )
