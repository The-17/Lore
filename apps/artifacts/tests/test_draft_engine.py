from django.test import Client, TestCase
from apps.accounts.models import Principal, User
from apps.artifacts.models import Artifact, ArtifactDraft, LifecycleState, SkillArtifact
from apps.artifacts.services import create_initial_version


class DraftEngineTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email="author@lore.dev", password="testpassword123", first_name="Draft", last_name="Author"
        )
        self.principal = Principal.objects.create(
            kind=Principal.Kind.USER, display_name="Draft Author", user=self.user
        )
        self.tokens = self.user.tokens()
        self.headers = {"HTTP_AUTHORIZATION": f"Bearer {self.tokens['access']}"}

        self.artifact = Artifact.objects.create(
            type="skill",
            title="ADM Architecture",
            owner=self.principal,
            created_by=self.principal,
            inherit_permissions=False,
            lifecycle_state=LifecycleState.DRAFT,
        )
        SkillArtifact.objects.create(artifact=self.artifact, skill_md_content="# Original Title\n\nOriginal body text.")
        create_initial_version(self.artifact, "# Original Title\n\nOriginal body text.", self.principal)

    def test_draft_lifecycle_flow(self):
        # 1. Check GET draft when no draft exists -> 404
        res = self.client.get(f"/api/artifacts/{self.artifact.id}/draft", **self.headers)
        self.assertEqual(res.status_code, 404)

        # 2. Patch draft to create and modify blocks
        patch_payload = {
            "operations": [
                {
                    "op": "insert_block",
                    "block_id": "blk_1",
                    "block_type": "heading",
                    "content": "ADM Architecture Updated",
                    "attrs": {"level": 1},
                },
                {
                    "op": "insert_block",
                    "block_id": "blk_2",
                    "block_type": "paragraph",
                    "content": "This is live working draft content.",
                    "attrs": {},
                },
            ]
        }
        res = self.client.patch(
            f"/api/artifacts/{self.artifact.id}/draft",
            data=patch_payload,
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(len(data["block_data"]), 2)
        self.assertEqual(data["block_data"][0]["id"], "blk_1")
        self.assertEqual(data["block_data"][1]["id"], "blk_2")

        # 3. Verify GET draft returns active draft
        res = self.client.get(f"/api/artifacts/{self.artifact.id}/draft", **self.headers)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()["block_data"]), 2)

        # 4. Commit draft to create version 2
        commit_payload = {"commit_message": "Finalized ADM update"}
        res = self.client.post(
            f"/api/artifacts/{self.artifact.id}/commit",
            data=commit_payload,
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(res.status_code, 200)
        self.artifact.refresh_from_db()
        self.assertEqual(self.artifact.current_version.version_number, 2)
        self.assertEqual(self.artifact.current_version.commit_message, "Finalized ADM update")

        # Verify draft was deleted post-commit
        self.assertFalse(ArtifactDraft.objects.filter(artifact=self.artifact).exists())

        # 5. Verify discard draft
        # Create a new draft
        self.client.patch(
            f"/api/artifacts/{self.artifact.id}/draft",
            data={"operations": [{"op": "insert_block", "block_id": "blk_tmp", "content": "temporary"}]},
            content_type="application/json",
            **self.headers,
        )
        self.assertTrue(ArtifactDraft.objects.filter(artifact=self.artifact).exists())

        # Discard it
        res = self.client.delete(f"/api/artifacts/{self.artifact.id}/draft", **self.headers)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(ArtifactDraft.objects.filter(artifact=self.artifact).exists())
