import argparse
import os
import sys
from types import SimpleNamespace
from typing import Any, Optional

# Setup Django environment before importing models
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lore.settings.base")

import django  # noqa: E402
django.setup()

from fastmcp import FastMCP  # noqa: E402
from apps.accounts.models import AgentToken, Principal, User  # noqa: E402
from apps.mcp.tools import (  # noqa: E402
    mcp_commit_artifact_version,
    mcp_create_collection,
    mcp_create_relationship,
    mcp_delete_artifact,
    mcp_get_related_artifacts,
    mcp_list_collection,
    mcp_list_skills,
    mcp_read_artifact,
    mcp_revert_artifact,
    mcp_search_artifacts,
    mcp_search_artifacts_semantic,
    mcp_update_artifact_draft,
    mcp_write_artifact,
)

mcp = FastMCP(
    name="Lore Artifact Plane",
    instructions="Native Model Context Protocol (MCP) interface for the Lore Artifact Plane.",
)

_global_token_str: Optional[str] = None


def get_authenticated_request_context(token_str: Optional[str] = None) -> SimpleNamespace:
    """
    Constructs an authenticated request-like context for MCP tool execution.
    Authenticates against AgentToken or falls back to workspace admin principal.
    """
    active_token = token_str or _global_token_str or os.environ.get("LORE_AGENT_TOKEN")
    agent_token = None

    if active_token:
        # Check if prefixed token
        if active_token.startswith("lore_agt_"):
            parts = active_token.split("_")
            if len(parts) >= 4:
                lookup_id = parts[2]
                agent_token = AgentToken.objects.select_related("user", "principal").filter(lookup_id=lookup_id).first()
        
        if not agent_token:
            import hashlib
            th = hashlib.sha256(active_token.encode("utf-8")).hexdigest()
            agent_token = AgentToken.objects.select_related("user", "principal").filter(token_hash=th).first()

        if agent_token and agent_token.verify_secret(active_token):
            return SimpleNamespace(
                user=agent_token.user,
                principal=agent_token.principal,
                agent_token=agent_token,
            )

    # Fallback to first active admin principal in workspace
    admin_user = User.objects.filter(is_workspace_admin=True).first() or User.objects.first()
    principal = getattr(admin_user, "principal", None) if admin_user else None

    return SimpleNamespace(
        user=admin_user,
        principal=principal,
        agent_token=None,
    )


@mcp.tool()
def search_artifacts(query: str = "", limit: int = 5, collection_id: Optional[str] = None) -> list[dict[str, Any]]:
    """Search for artifacts by title or text content across accessible collections."""
    req = get_authenticated_request_context()
    return mcp_search_artifacts(req, query=query, limit=limit, collection_id=collection_id)


@mcp.tool()
def search_artifacts_semantic(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Perform semantic RAG retrieval across granular text chunks and return matching knowledge context."""
    req = get_authenticated_request_context()
    return mcp_search_artifacts_semantic(req, query=query, limit=limit)


@mcp.tool()
def read_artifact(artifact_id: str) -> dict[str, Any]:
    """Retrieve full artifact metadata, current version, and text content by ID."""
    req = get_authenticated_request_context()
    return mcp_read_artifact(req, artifact_id=artifact_id)


@mcp.tool()
def update_artifact_draft(artifact_id: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Apply mutation operations to an artifact's working draft (collaborative ADM editing).
    Operations can include insert_block, replace_block, move_block, delete_block.
    """
    req = get_authenticated_request_context()
    return mcp_update_artifact_draft(req, artifact_id=artifact_id, operations=operations)


@mcp.tool()
def commit_artifact_version(artifact_id: str, commit_message: str = "") -> dict[str, Any]:
    """Commit the active working draft into an immutable, permanent artifact version snapshot."""
    req = get_authenticated_request_context()
    return mcp_commit_artifact_version(req, artifact_id=artifact_id, commit_message=commit_message)


@mcp.tool()
def write_artifact(
    title: str,
    type: str,
    content: str,
    collection_id: Optional[str] = None,
    expected_version_number: Optional[int] = None,
) -> dict[str, Any]:
    """
    Create or update an artifact, automatically generating version diffs and wiki-link references.
    Type can be: 'skill', 'decision', 'memory', 'document'.
    """
    req = get_authenticated_request_context()
    return mcp_write_artifact(
        req,
        title=title,
        type=type,
        content=content,
        collection_id=collection_id,
        expected_version_number=expected_version_number,
    )


@mcp.tool()
def delete_artifact(artifact_id: str) -> dict[str, Any]:
    """Soft-delete an artifact by setting its deleted_at timestamp."""
    req = get_authenticated_request_context()
    return mcp_delete_artifact(req, artifact_id=artifact_id)


@mcp.tool()
def revert_artifact(artifact_id: str, target_version_number: int, commit_message: str = "") -> dict[str, Any]:
    """Revert an artifact to a previous version number, creating an append-only snapshot."""
    req = get_authenticated_request_context()
    return mcp_revert_artifact(
        req,
        artifact_id=artifact_id,
        target_version_number=target_version_number,
        commit_message=commit_message,
    )


@mcp.tool()
def list_collection(collection_id: Optional[str] = None) -> dict[str, Any]:
    """List sub-collections and contained artifacts inside a collection."""
    req = get_authenticated_request_context()
    return mcp_list_collection(req, collection_id=collection_id)


@mcp.tool()
def create_collection(name: str, parent_id: Optional[str] = None, description: str = "") -> dict[str, Any]:
    """Create a new collection for organizing artifacts."""
    req = get_authenticated_request_context()
    return mcp_create_collection(req, name=name, parent_id=parent_id, description=description)


@mcp.tool()
def create_relationship(from_artifact_id: str, to_artifact_id: str, relation_type: str = "references") -> dict[str, Any]:
    """Create a directed relationship edge between two artifacts in the knowledge graph."""
    req = get_authenticated_request_context()
    return mcp_create_relationship(
        req,
        from_artifact_id=from_artifact_id,
        to_artifact_id=to_artifact_id,
        relation_type=relation_type,
    )


@mcp.tool()
def get_related_artifacts(artifact_id: str) -> dict[str, Any]:
    """Retrieve incoming and outgoing relationship edges for an artifact."""
    req = get_authenticated_request_context()
    return mcp_get_related_artifacts(req, artifact_id=artifact_id)


@mcp.tool()
def list_skills() -> list[dict[str, Any]]:
    """List all registered skills and prompt templates available in Lore."""
    req = get_authenticated_request_context()
    return mcp_list_skills(req)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lore Artifact Plane FastMCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport protocol (stdio or sse)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host for SSE transport")
    parser.add_argument("--port", type=int, default=8001, help="Port for SSE transport")
    parser.add_argument("--token", default=None, help="Agent token for authentication")

    args = parser.parse_args()
    if args.token:
        _global_token_str = args.token

    if args.transport == "sse":
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")
