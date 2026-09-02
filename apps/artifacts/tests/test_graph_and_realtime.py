from django.test import Client, TestCase
from apps.accounts.models import Principal, User
from apps.artifacts.models import Artifact, ArtifactRelationship, LifecycleState, SkillArtifact
from apps.artifacts.services import create_initial_version, update_artifact_version
from apps.artifacts.wiki_links import extract_and_sync_wiki_links, parse_wiki_link_titles
from apps.common.realtime import publish_artifact_event, subscribe_events, unsubscribe_events


class GraphAndRealtimeTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email="researcher@lore.dev", password="testpassword123", first_name="Graph", last_name="Researcher"
        )
        self.principal = Principal.objects.create(
            kind=Principal.Kind.USER, display_name="Graph Researcher", user=self.user
        )
        self.tokens = self.user.tokens()
        self.headers = {"HTTP_AUTHORIZATION": f"Bearer {self.tokens['access']}"}

        # Artifact 1
        self.art1 = Artifact.objects.create(
            type="skill",
            title="Distributed Systems",
            owner=self.principal,
            created_by=self.principal,
            inherit_permissions=False,
            lifecycle_state=LifecycleState.APPROVED,
        )
        SkillArtifact.objects.create(artifact=self.art1, skill_md_content="Distributed fundamentals.")
        create_initial_version(self.art1, "Distributed fundamentals.", self.principal)

        # Artifact 2
        self.art2 = Artifact.objects.create(
            type="skill",
            title="Consensus Raft",
            owner=self.principal,
            created_by=self.principal,
            inherit_permissions=False,
            lifecycle_state=LifecycleState.APPROVED,
        )
        SkillArtifact.objects.create(artifact=self.art2, skill_md_content="Raft protocol details.")
        create_initial_version(self.art2, "Raft protocol details.", self.principal)

    def test_wikilink_alias_parsing(self):
        text = "See [[Consensus Raft|Raft Paper]] and [[Distributed Systems]] for context."
        titles = parse_wiki_link_titles(text)
        self.assertIn("Consensus Raft", titles)
        self.assertIn("Distributed Systems", titles)
        self.assertEqual(len(titles), 2)

    def test_wikilink_sync_and_stale_edge_pruning(self):
        # 1. Add reference link from art1 to art2 with alias
        content_with_link = "We implement [[Consensus Raft|Raft]] here."
        extract_and_sync_wiki_links(self.art1, content_with_link, self.principal)

        self.assertTrue(
            ArtifactRelationship.objects.filter(
                from_artifact=self.art1,
                to_artifact=self.art2,
                relation_type="references",
            ).exists()
        )

        # 2. Update content removing the link
        content_without_link = "No links in this text anymore."
        extract_and_sync_wiki_links(self.art1, content_without_link, self.principal)

        # Edge should be pruned
        self.assertFalse(
            ArtifactRelationship.objects.filter(
                from_artifact=self.art1,
                to_artifact=self.art2,
                relation_type="references",
            ).exists()
        )

    def test_get_graph_endpoint(self):
        # Establish relationship
        ArtifactRelationship.objects.create(
            from_artifact=self.art1,
            to_artifact=self.art2,
            relation_type="depends_on",
            created_by=self.principal,
        )

        res = self.client.get("/api/artifacts/graph", **self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertIn("nodes", data)
        self.assertIn("edges", data)
        node_ids = {n["id"] for n in data["nodes"]}
        self.assertIn(str(self.art1.id), node_ids)
        self.assertIn(str(self.art2.id), node_ids)

        self.assertEqual(len(data["edges"]), 1)
        edge = data["edges"][0]
        self.assertEqual(edge["source_id"], str(self.art1.id))
        self.assertEqual(edge["target_id"], str(self.art2.id))
        self.assertEqual(edge["relation_type"], "depends_on")

    def test_sse_stream_query_token_auth(self):
        # Test connecting to SSE endpoint using ?token=<jwt>
        token = self.tokens["access"]
        res = self.client.get(f"/api/artifacts/stream/events?token={token}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "text/event-stream")

    def test_realtime_event_pubsub(self):
        q = subscribe_events()
        try:
            publish_artifact_event(
                event_type="artifact.created",
                artifact_id=str(self.art1.id),
                payload={"title": self.art1.title},
                owner_id=str(self.art1.owner_id),
            )
            event = q.get_nowait()
            self.assertEqual(event["event"], "artifact.created")
            self.assertEqual(event["artifact_id"], str(self.art1.id))
            self.assertEqual(event["payload"]["title"], "Distributed Systems")
        finally:
            unsubscribe_events(q)
