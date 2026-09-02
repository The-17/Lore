from uuid import uuid4
from django.test import Client, TestCase

import mcp_server
from apps.accounts.models import Principal, User
from apps.artifacts.models import Artifact, ArtifactDraft, LifecycleState, SkillArtifact
from apps.artifacts.services import create_initial_version
from apps.mcp.tools import (
    mcp_commit_artifact_version,
    mcp_read_artifact,
    mcp_search_artifacts,
    mcp_search_artifacts_semantic,
    mcp_update_artifact_draft,
    mcp_write_artifact,
)


class MCPEngineTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email="mcp_agent@lore.dev",
            password="testpassword123",
            first_name="FastMCP",
            last_name="Agent",
            is_workspace_admin=True,
        )
        self.principal = Principal.objects.create(
            kind=Principal.Kind.USER, display_name="FastMCP Agent", user=self.user
        )
        self.tokens = self.user.tokens()
        self.headers = {"HTTP_AUTHORIZATION": f"Bearer {self.tokens['access']}"}

        # Create initial test artifact
        self.art = Artifact.objects.create(
            type="skill",
            title="FastMCP Guidelines",
            owner=self.principal,
            created_by=self.principal,
            inherit_permissions=False,
            lifecycle_state=LifecycleState.APPROVED,
        )
        SkillArtifact.objects.create(
            artifact=self.art,
            skill_md_content="Guidelines for implementing FastMCP tools and RAG retrieval.",
        )
        create_initial_version(self.art, "Guidelines for implementing FastMCP tools and RAG retrieval.", self.principal)

        from types import SimpleNamespace
        self.request_context = SimpleNamespace(
            user=self.user,
            principal=self.principal,
            agent_token=None,
        )

    def test_mcp_server_tools_registered(self):
        # Verify FastMCP server initialized and registered tools
        self.assertIsNotNone(mcp_server.mcp)
        # Verify FastMCP name
        self.assertEqual(mcp_server.mcp.name, "Lore Artifact Plane")

    def test_mcp_read_and_search_tools(self):
        # 1. Read artifact
        read_res = mcp_read_artifact(self.request_context, str(self.art.id))
        self.assertEqual(read_res["id"], str(self.art.id))
        self.assertEqual(read_res["title"], "FastMCP Guidelines")
        self.assertIn("Guidelines for implementing", read_res["content"])

        # 2. Search artifacts
        search_res = mcp_search_artifacts(self.request_context, query="FastMCP")
        self.assertTrue(len(search_res) >= 1)
        self.assertEqual(search_res[0]["id"], str(self.art.id))

    def test_mcp_draft_patch_and_commit(self):
        # 1. Patch draft via MCP tool
        patch_ops = [
            {
                "op": "insert_block",
                "block_id": "blk_mcp_1",
                "block_type": "heading",
                "content": "Collaborative MCP Header",
                "attrs": {"level": 1},
            },
            {
                "op": "insert_block",
                "block_id": "blk_mcp_2",
                "block_type": "paragraph",
                "content": "Written autonomously by agent via FastMCP tool.",
                "attrs": {},
            },
        ]
        draft_res = mcp_update_artifact_draft(self.request_context, str(self.art.id), patch_ops)
        self.assertEqual(draft_res["status"], "success")
        self.assertEqual(draft_res["block_count"], 2)

        # 2. Commit draft via MCP tool
        commit_res = mcp_commit_artifact_version(
            self.request_context, str(self.art.id), commit_message="Agent draft commit"
        )
        self.assertEqual(commit_res["status"], "success")
        self.assertEqual(commit_res["version_number"], 2)

        self.art.refresh_from_db()
        self.assertEqual(self.art.current_version.version_number, 2)
        self.assertFalse(ArtifactDraft.objects.filter(artifact=self.art).exists())

    def test_mcp_semantic_rag_search(self):
        results = mcp_search_artifacts_semantic(self.request_context, query="FastMCP RAG", limit=5)
        self.assertTrue(isinstance(results, list))
        if results:
            self.assertEqual(results[0]["artifact_id"], str(self.art.id))

    def test_mcp_jsonrpc_http_endpoint(self):
        # 1. tools/list
        res = self.client.post(
            "/api/mcp/",
            data={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(res.status_code, 200)
        tools = res.json()["result"]["tools"]
        tool_names = {t["name"] for t in tools}
        self.assertIn("search_artifacts", tool_names)
        self.assertIn("read_artifact", tool_names)
        self.assertIn("update_artifact_draft", tool_names)
        self.assertIn("commit_artifact_version", tool_names)
        self.assertIn("search_artifacts_semantic", tool_names)

        # 2. tools/call read_artifact
        call_payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "read_artifact",
                "arguments": {"artifact_id": str(self.art.id)},
            },
        }
        res = self.client.post(
            "/api/mcp/",
            data=call_payload,
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(res.status_code, 200)
        content = res.json()["result"]["content"]
        self.assertTrue(len(content) > 0)
        self.assertIn("FastMCP Guidelines", content[0]["text"])
