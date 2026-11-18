import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.routers import standards as standards_router
from app.schemas import TeamStandardCreate, TeamStandardUpdate


class TeamStandardRouteTests(unittest.TestCase):
    def setUp(self):
        self.records = {}

        def create(**fields):
            standard_id = "standard-1"
            self.records[standard_id] = {"id": standard_id, **fields}
            return dict(self.records[standard_id])

        def update(standard_id, **fields):
            self.records[standard_id].update(fields)
            return {"id": standard_id, **fields}

        self.patches = [
            patch.object(standards_router, "firebase_available", return_value=True),
            patch.object(standards_router, "create_team_standard", side_effect=create),
            patch.object(standards_router, "get_team_standard", side_effect=lambda key: self.records.get(key)),
            patch.object(standards_router, "update_team_standard", side_effect=update),
            patch.object(standards_router, "delete_team_standard", side_effect=lambda key: self.records.pop(key)),
            patch.object(
                standards_router,
                "ensure_default_team_standards",
                side_effect=lambda _user: list(self.records.values()),
            ),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_create_edit_toggle_and_delete_standard(self):
        created = standards_router.create_standard_route(
            TeamStandardCreate(
                title="Use validated input",
                description="Validate all input before use.",
                category="security",
                severity="error",
                recommended_solution="Validate type and range.",
                enabled=True,
                project_id="global",
                created_by="user-1",
            ),
            authenticated_user="user-1",
        )
        self.assertEqual(created["title"], "Use validated input")
        self.assertTrue(created["enabled"])

        edited = standards_router.update_standard_route(
            "standard-1",
            TeamStandardUpdate(
                title="Validate request input",
                description="Validate user input.",
                category="security",
                severity="critical",
                recommended_solution="Reject invalid values.",
                enabled=False,
            ),
            authenticated_user="user-1",
        )
        self.assertEqual(edited["title"], "Validate request input")
        self.assertEqual(edited["severity"], "critical")
        self.assertFalse(edited["enabled"])

        reenabled = standards_router.update_standard_route(
            "standard-1",
            TeamStandardUpdate(
                title=edited["title"],
                description=edited["description"],
                category=edited["category"],
                severity=edited["severity"],
                recommended_solution=edited["recommended_solution"],
                enabled=True,
            ),
            authenticated_user="user-1",
        )
        self.assertTrue(reenabled["enabled"])
        self.assertIsNone(
            standards_router.delete_standard_route("standard-1", authenticated_user="user-1")
        )
        self.assertNotIn("standard-1", self.records)

    def test_loaded_document_id_can_be_toggled_and_survives_reload(self):
        created = standards_router.create_standard_route(
            TeamStandardCreate(
                title="Never hardcode credentials",
                description="Keep credentials out of source code.",
                category="security",
                severity="critical",
                recommended_solution="Use an environment variable.",
                enabled=True,
                project_id="global",
                created_by="user-1",
            ),
            authenticated_user="user-1",
        )

        loaded = standards_router.get_standards_route(authenticated_user="user-1")
        self.assertEqual(loaded[0]["id"], created["id"])
        disabled = standards_router.update_standard_route(
            loaded[0]["id"],
            TeamStandardUpdate(
                title=loaded[0]["title"],
                description=loaded[0]["description"],
                category=loaded[0]["category"],
                severity=loaded[0]["severity"],
                recommended_solution=loaded[0]["recommended_solution"],
                enabled=False,
            ),
            authenticated_user="user-1",
        )
        self.assertFalse(disabled["enabled"])

        reloaded = standards_router.get_standards_route(authenticated_user="user-1")
        self.assertEqual(reloaded[0]["id"], loaded[0]["id"])
        self.assertFalse(reloaded[0]["enabled"])

        reenabled = standards_router.update_standard_route(
            reloaded[0]["id"],
            TeamStandardUpdate(
                title=reloaded[0]["title"],
                description=reloaded[0]["description"],
                category=reloaded[0]["category"],
                severity=reloaded[0]["severity"],
                recommended_solution=reloaded[0]["recommended_solution"],
                enabled=True,
            ),
            authenticated_user="user-1",
        )
        self.assertTrue(reenabled["enabled"])
        self.assertTrue(standards_router.get_standards_route(authenticated_user="user-1")[0]["enabled"])

    def test_update_missing_standard_returns_clear_not_found_error(self):
        with self.assertRaises(HTTPException) as error:
            standards_router.update_standard_route(
                "missing-standard",
                TeamStandardUpdate(
                    title="Some standard",
                    description="Some description.",
                    enabled=False,
                ),
                authenticated_user="user-1",
            )

        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(error.exception.detail, "Standard not found.")


if __name__ == "__main__":
    unittest.main()
