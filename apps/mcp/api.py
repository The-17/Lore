from typing import Any, Dict, Optional
from ninja import Router, Schema

from apps.mcp.tools import (
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

router = Router(tags=["Model Context Protocol (MCP)"])


class MCPPayloadSchema(Schema):
    jsonrpc: str = "2.0"
    id: Optional[Any] = None
    method: str
    params: Optional[Dict[str, Any]] = None

MCP_TOOLS_SPEC = [
    {
        "name": "search_artifacts",
        "description": "Search for artifacts by query string across accessible collections.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
                "collection_id": {"type": "string"},
            },
        },
    },
    {
        "name": "read_artifact",
        "description": "Retrieve full artifact metadata, version, and text content.",
        "inputSchema": {
            "type": "object",
            "properties": {"artifact_id": {"type": "string"}},
            "required": ["artifact_id"],
        },
    },
    {
        "name": "write_artifact",
        "description": "Create or update an artifact, automatically generating version diffs and wiki-link references.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "type": {"type": "string", "enum": ["skill", "decision", "memory", "document"]},
                "content": {"type": "string"},
                "collection_id": {"type": "string"},
                "expected_version_number": {"type": "integer"},
            },
            "required": ["title", "type", "content"],
        },
    },
    {
        "name": "delete_artifact",
        "description": "Soft-delete an artifact by setting deleted_at timestamp.",
        "inputSchema": {
            "type": "object",
            "properties": {"artifact_id": {"type": "string"}},
            "required": ["artifact_id"],
        },
    },
    {
        "name": "revert_artifact",
        "description": "Revert an artifact to a previous version number, recording an append-only version snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string"},
                "target_version_number": {"type": "integer"},
                "commit_message": {"type": "string"},
            },
            "required": ["artifact_id", "target_version_number"],
        },
    },
    {
        "name": "list_collection",
        "description": "List sub-collections and contained artifacts inside a parent collection.",
        "inputSchema": {
            "type": "object",
            "properties": {"collection_id": {"type": "string"}},
        },
    },
    {
        "name": "create_collection",
        "description": "Create a new collection for organizing artifacts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "parent_id": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "create_relationship",
        "description": "Create a typed graph edge between two artifacts (e.g. references, derived_from, depends_on).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_artifact_id": {"type": "string"},
                "to_artifact_id": {"type": "string"},
                "relation_type": {"type": "string"},
            },
            "required": ["from_artifact_id", "to_artifact_id", "relation_type"],
        },
    },
    {
        "name": "get_related_artifacts",
        "description": "Retrieve incoming and outgoing graph relationships for an artifact.",
        "inputSchema": {
            "type": "object",
            "properties": {"artifact_id": {"type": "string"}},
            "required": ["artifact_id"],
        },
    },
    {
        "name": "list_skills",
        "description": "List all shared reusable skills and system capabilities registered in Lore.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "update_artifact_draft",
        "description": "Apply typed mutation operations to an artifact's working draft (collaborative ADM editing).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string"},
                "operations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {"type": "string"},
                            "block_id": {"type": "string"},
                            "block_type": {"type": "string"},
                            "content": {},
                            "attrs": {"type": "object"},
                        },
                        "required": ["op"],
                    },
                },
            },
            "required": ["artifact_id", "operations"],
        },
    },
    {
        "name": "commit_artifact_version",
        "description": "Commit an active working draft into an immutable, permanent artifact version snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string"},
                "commit_message": {"type": "string"},
            },
            "required": ["artifact_id"],
        },
    },
    {
        "name": "search_artifacts_semantic",
        "description": "Perform semantic RAG retrieval across granular text chunks and return matching knowledge context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
]


@router.get("/tools", response=list[dict])
def get_mcp_tools(request):
    """Return the list of available MCP tools and their JSON schemas."""
    return MCP_TOOLS_SPEC


@router.post("/", response=dict)
def handle_mcp_jsonrpc(request, payload: MCPPayloadSchema):
    """
    JSON-RPC 2.0 endpoint handling MCP tool execution and discovery.
    """
    method = payload.method
    rpc_id = payload.id
    params = payload.params or {}

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {"tools": MCP_TOOLS_SPEC},
        }

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})

        try:
            if name == "search_artifacts":
                result = mcp_search_artifacts(request, **arguments)
            elif name == "read_artifact":
                result = mcp_read_artifact(request, **arguments)
            elif name == "write_artifact":
                result = mcp_write_artifact(request, **arguments)
            elif name == "delete_artifact":
                result = mcp_delete_artifact(request, **arguments)
            elif name == "revert_artifact":
                result = mcp_revert_artifact(request, **arguments)
            elif name == "list_collection":
                result = mcp_list_collection(request, **arguments)
            elif name == "create_collection":
                result = mcp_create_collection(request, **arguments)
            elif name == "create_relationship":
                result = mcp_create_relationship(request, **arguments)
            elif name == "get_related_artifacts":
                result = mcp_get_related_artifacts(request, **arguments)
            elif name == "list_skills":
                result = mcp_list_skills(request)
            elif name == "update_artifact_draft":
                result = mcp_update_artifact_draft(request, **arguments)
            elif name == "commit_artifact_version":
                result = mcp_commit_artifact_version(request, **arguments)
            elif name == "search_artifacts_semantic":
                result = mcp_search_artifacts_semantic(request, **arguments)
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {name}"},
                }

            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {"content": [{"type": "text", "text": str(result)}]},
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32603, "message": str(e)},
            }

    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {"code": -32601, "message": f"Method not supported: {method}"},
    }

