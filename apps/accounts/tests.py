from django.test import Client, TestCase

from apps.accounts.models import Invite, User


class AccountsNinjaAPITests(TestCase):
    def setUp(self):
        self.client = Client()
        self.register_data = {
            "first_name": "Admin",
            "last_name": "User",
            "email": "admin@example.com",
            "password": "strongpassword123",
        }
        self.login_data = {
            "email": "admin@example.com",
            "password": "strongpassword123",
        }

    def test_auth_and_token_flow(self):
        # 1. Register Workspace Admin (first user)
        res = self.client.post(
            "/api/auth/register",
            data=self.register_data,
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 201)
        self.assertIn("Workspace initialized", res.json()["message"])
        self.assertEqual(res.json()["data"]["email"], "admin@example.com")

        # Verify the user has is_workspace_admin=True
        admin_user = User.objects.get(email="admin@example.com")
        self.assertTrue(admin_user.is_workspace_admin)

        # 2. Try to register another user without an invite (must fail with 403)
        second_register_data = {
            "first_name": "Second",
            "last_name": "User",
            "email": "second@example.com",
            "password": "anotherpassword123",
        }
        res = self.client.post(
            "/api/auth/register",
            data=second_register_data,
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("invite-only", res.json()["message"])

        # 3. Login Admin User to get JWT
        res = self.client.post(
            "/api/auth/login",
            data=self.login_data,
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "success")

        admin_jwt = res.json()["data"]["access_token"]
        admin_headers = {"HTTP_AUTHORIZATION": f"Bearer {admin_jwt}"}

        # 4. Create an invite for a collaborator
        invite_data = {
            "email": "collab@example.com",
            "name": "Bob Collaborator",
        }
        res = self.client.post(
            "/api/auth/invites",
            data=invite_data,
            content_type="application/json",
            **admin_headers,
        )
        self.assertEqual(res.status_code, 201)
        invite_token = res.json()["token"]
        invite_id = res.json()["id"]
        self.assertIsNotNone(invite_token)

        # 5. List invites
        res = self.client.get("/api/auth/invites", **admin_headers)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()), 1)
        self.assertEqual(res.json()[0]["email"], "collab@example.com")
        # Raw token should NOT be in the list for security
        self.assertNotIn("token", res.json()[0])
        self.assertEqual(res.json()[0]["token_prefix"], invite_token[:8])

        # 6. Preview/Validate the invite token publicly (unauthenticated)
        res = self.client.get(f"/api/auth/invites/{invite_token}/preview")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["email"], "collab@example.com")
        self.assertEqual(res.json()["name"], "Bob Collaborator")

        # 7. Claim the invite to create the collaborator user (unauthenticated)
        claim_data = {
            "first_name": "Bob",
            "last_name": "Collaborator",
            "password": "collabpassword123",
        }
        res = self.client.post(
            f"/api/auth/invites/{invite_token}/claim",
            data=claim_data,
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 201)
        self.assertIn("Welcome to the workspace", res.json()["message"])

        collab_jwt = res.json()["data"]["access_token"]
        collab_headers = {"HTTP_AUTHORIZATION": f"Bearer {collab_jwt}"}

        # Verify the new user is NOT workspace admin by default
        collab_user = User.objects.get(email="collab@example.com")
        self.assertFalse(collab_user.is_workspace_admin)

        # Verify invite is marked claimed
        invite_obj = Invite.objects.get(id=invite_id)
        self.assertTrue(invite_obj.claimed)
        self.assertIsNotNone(invite_obj.claimed_at)

        # 8. Try to claim same invite again (should fail)
        res = self.client.post(
            f"/api/auth/invites/{invite_token}/claim",
            data=claim_data,
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("already been used", res.json()["message"])

        # 9. Verify collaborator cannot manage invites
        res = self.client.post(
            "/api/auth/invites",
            data={"email": "hacker@example.com", "name": "Hacker"},
            content_type="application/json",
            **collab_headers,
        )
        self.assertEqual(res.status_code, 403)

        res = self.client.get("/api/auth/invites", **collab_headers)
        self.assertEqual(res.status_code, 403)

        # 10. Test revoking invites (Admin issues another invite, then revokes it)
        res = self.client.post(
            "/api/auth/invites",
            data={"email": "revoked@example.com"},
            content_type="application/json",
            **admin_headers,
        )
        self.assertEqual(res.status_code, 201)
        revoked_id = res.json()["id"]

        # Revoke it
        res = self.client.delete(f"/api/auth/invites/{revoked_id}", **admin_headers)
        self.assertEqual(res.status_code, 204)

        # Verify invite is gone
        res = self.client.get("/api/auth/invites", **admin_headers)
        # Should only have the first (claimed) one now
        self.assertEqual(len(res.json()), 1)

        # 11. Test Agent Token creation (using admin headers)
        token_data = {
            "description": "Local test agent key",
            "scope": "read_write",
        }
        res = self.client.post(
            "/api/auth/tokens",
            data=token_data,
            content_type="application/json",
            **admin_headers,
        )
        self.assertEqual(res.status_code, 201)

        token_id = res.json()["id"]
        agent_token_key = res.json()["token"]
        self.assertTrue(agent_token_key.startswith("lore_agent_") or agent_token_key.startswith("lore_agt_"))

        # 12. List Agent Tokens
        res = self.client.get("/api/auth/tokens", **admin_headers)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()), 1)
        self.assertEqual(res.json()[0]["id"], token_id)

        # 13. Authenticate with Agent Token key on Protected Collections Endpoint
        agent_headers = {"HTTP_AUTHORIZATION": f"Bearer {agent_token_key}"}
        res = self.client.get("/api/collections/", **agent_headers)
        self.assertEqual(res.status_code, 200)

        # 14. Revoke Agent Token
        res = self.client.delete(f"/api/auth/tokens/{token_id}", **admin_headers)
        self.assertEqual(res.status_code, 204)

        # 15. Verify Revoked Agent Token fails authentication
        res = self.client.get("/api/collections/", **agent_headers)
        self.assertEqual(res.status_code, 401)
