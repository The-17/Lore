from django.test import TestCase
from apps.accounts.models import AgentToken, Principal, User
from apps.artifacts.models import Artifact, ArtifactRelationship, DecisionArtifact, LifecycleState, SkillArtifact
from apps.artifacts.services import create_initial_version, revert_artifact_to_version, update_artifact_version
from apps.artifacts.wiki_links import extract_and_sync_wiki_links


class VersioningAndGovernanceTestCase(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(email="user1@lore.com", password="password123", first_name="User", last_name="One")
        self.principal1 = Principal.objects.create(kind="user", user=self.user1)

        self.user2 = User.objects.create_user(email="user2@lore.com", password="password123", first_name="User", last_name="Two")
        self.principal2 = Principal.objects.create(kind="user", user=self.user2)

        self.agent = Principal.objects.create(kind="agent_token", display_name="Agent Alpha")
        self.token = AgentToken.objects.create(user=self.user1, principal=self.agent, description="Test Token", token_hash="hash123")

    def test_text_artifact_versioning_and_revert(self):
        # Create initial skill
        artifact = Artifact.objects.create(
            type="skill",
            title="Python Ninja",
            owner=self.principal1,
            created_by=self.principal1,
            inherit_permissions=False,
            lifecycle_state=LifecycleState.DRAFT,
        )
        SkillArtifact.objects.create(artifact=artifact, skill_md_content="v1 text")
        create_initial_version(artifact, "v1 text", self.principal1)

        self.assertEqual(artifact.versions.count(), 1)
        self.assertEqual(artifact.current_version.version_number, 1)

        # Update text artifact content
        v2 = update_artifact_version(artifact, "v2 text content", self.principal1, commit_message="Update to v2")
        self.assertEqual(artifact.versions.count(), 2)
        self.assertEqual(artifact.current_version.version_number, 2)
        self.assertEqual(artifact.skill.skill_md_content, "v2 text content")

        # Revert to version 1
        v1_obj = artifact.versions.get(version_number=1)
        v3 = revert_artifact_to_version(artifact, v1_obj, self.principal1, commit_message="Revert to v1")

        self.assertEqual(artifact.versions.count(), 3)
        self.assertEqual(artifact.current_version.version_number, 3)
        self.assertEqual(artifact.skill.skill_md_content, "v1 text")

    def test_wiki_link_tenant_isolation(self):
        # Create target artifact owned by User 2
        user2_art = Artifact.objects.create(
            type="skill",
            title="Secret Internal Architecture",
            owner=self.principal2,
            created_by=self.principal2,
            inherit_permissions=False,
        )
        SkillArtifact.objects.create(artifact=user2_art, skill_md_content="Secret content")

        # Create source artifact owned by User 1 referencing User 2's title
        user1_art = Artifact.objects.create(
            type="skill",
            title="Public Integration",
            owner=self.principal1,
            created_by=self.principal1,
            inherit_permissions=False,
        )

        resolved = extract_and_sync_wiki_links(
            user1_art,
            "Mentions [[Secret Internal Architecture]]",
            self.principal1,
        )

        # User 1 should NOT be able to link to User 2's artifact
        self.assertEqual(resolved, [])
        self.assertFalse(
            ArtifactRelationship.objects.filter(
                from_artifact=user1_art,
                to_artifact=user2_art,
            ).exists()
        )

    def test_initial_lifecycle_state_policy(self):
        from unittest.mock import MagicMock
        from apps.artifacts.api import _initial_lifecycle_state

        # 1. Human Request (no agent_token)
        human_req = MagicMock(user=self.user1, agent_token=None)
        self.assertEqual(_initial_lifecycle_state("approved", human_req), LifecycleState.APPROVED)
        self.assertEqual(_initial_lifecycle_state("published", human_req), LifecycleState.PUBLISHED)

        # 2. Untrusted Agent Request (can_auto_approve=False)
        agent_p1 = Principal.objects.create(kind="agent_token", display_name="Untrusted Agent")
        untrusted_token = AgentToken.objects.create(
            user=self.user1, principal=agent_p1, description="Untrusted", token_hash="hash_untrusted", can_auto_approve=False
        )
        untrusted_req = MagicMock(user=self.user1, agent_token=untrusted_token)
        self.assertEqual(_initial_lifecycle_state("approved", untrusted_req), LifecycleState.DRAFT)
        self.assertEqual(_initial_lifecycle_state("review", untrusted_req), LifecycleState.REVIEW)

        # 3. Trusted Agent Request (can_auto_approve=True)
        agent_p2 = Principal.objects.create(kind="agent_token", display_name="Trusted Agent")
        trusted_token = AgentToken.objects.create(
            user=self.user1, principal=agent_p2, description="Trusted", token_hash="hash_trusted", can_auto_approve=True
        )
        trusted_req = MagicMock(user=self.user1, agent_token=trusted_token)
        self.assertEqual(_initial_lifecycle_state("approved", trusted_req), LifecycleState.APPROVED)
        self.assertEqual(_initial_lifecycle_state("published", trusted_req), LifecycleState.PUBLISHED)

    def test_version_diff_endpoint(self):
        from django.test import Client
        client = Client()
        tokens = self.user1.tokens()
        headers = {"HTTP_AUTHORIZATION": f"Bearer {tokens['access']}"}

        artifact = Artifact.objects.create(
            type="skill",
            title="Diff Test Skill",
            owner=self.principal1,
            created_by=self.principal1,
            inherit_permissions=False,
            lifecycle_state=LifecycleState.DRAFT,
        )
        SkillArtifact.objects.create(artifact=artifact, skill_md_content="line 1\nline 2\n")
        create_initial_version(artifact, "line 1\nline 2\n", self.principal1)

        update_artifact_version(artifact, "line 1\nline 2 modified\nline 3\n", self.principal1, commit_message="v2")

        res = client.get(f"/api/artifacts/{artifact.id}/diff?from_version=1&to_version=2", **headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["from_version"], 1)
        self.assertEqual(data["to_version"], 2)
        self.assertIn("+line 2 modified", data["diff"])
        self.assertIn("+line 3", data["diff"])
