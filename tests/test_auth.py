import base64
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from advisor.auth import CredentialStore
from advisor.server import create_server


class CredentialStoreTests(unittest.TestCase):
    def test_passwords_are_hashed_and_accounts_can_be_managed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "users.json"
            store = CredentialStore(
                path,
                bootstrap_username="marco",
                bootstrap_password="initial-secret",
            )

            self.assertNotIn("initial-secret", path.read_text(encoding="utf-8"))
            self.assertTrue(store.authenticate("marco", "initial-secret")["is_admin"])
            store.create_user("collaboratore", "second-secret")
            self.assertFalse(store.authenticate("collaboratore", "second-secret")["is_admin"])
            store.change_password("collaboratore", "second-secret", "changed-secret")
            self.assertIsNone(store.authenticate("collaboratore", "second-secret"))
            self.assertIsNotNone(store.authenticate("collaboratore", "changed-secret"))
            store.delete_user("collaboratore", requested_by="marco")
            self.assertEqual([user["username"] for user in store.list_users()], ["marco"])


class AccountApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.server = create_server(
            ("127.0.0.1", 0),
            profiles_dir=root / "profiles",
            datasets_dir=root / "datasets",
            uploads_dir=root / "uploads",
            updates_dir=root / "updates",
            auth_username="marco",
            auth_password="initial-secret",
            auth_file=root / "users.json",
        )
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.temporary.cleanup()

    @staticmethod
    def authorization(username, password):
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def request(self, method, path, username, password, payload=None):
        headers = self.authorization(username, password)
        body = None
        if payload is not None:
            body = json.dumps(payload)
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection(*self.server.server_address)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        content = response.read()
        parsed = json.loads(content) if content else None
        connection.close()
        return response.status, parsed

    def test_admin_creates_user_and_user_changes_own_password(self):
        status, session = self.request("GET", "/api/auth/session", "marco", "initial-secret")
        self.assertEqual(status, 200)
        self.assertEqual(session["username"], "marco")
        self.assertTrue(session["is_admin"])

        status, created = self.request(
            "POST",
            "/api/auth/users",
            "marco",
            "initial-secret",
            {"username": "ospite", "password": "guest-secret"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(created["user"]["username"], "ospite")

        status, _ = self.request("GET", "/api/auth/users", "ospite", "guest-secret")
        self.assertEqual(status, 403)
        status, changed = self.request(
            "PUT",
            "/api/auth/password",
            "ospite",
            "guest-secret",
            {"current_password": "guest-secret", "new_password": "new-guest-secret"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(changed["changed"])
        status, _ = self.request("GET", "/api/auth/session", "ospite", "guest-secret")
        self.assertEqual(status, 401)
        status, session = self.request("GET", "/api/auth/session", "ospite", "new-guest-secret")
        self.assertEqual(status, 200)
        self.assertEqual(session["username"], "ospite")


if __name__ == "__main__":
    unittest.main()
