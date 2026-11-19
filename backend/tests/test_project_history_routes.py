import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.routers import projects


class ProjectHistoryRouteTests(unittest.TestCase):
    def test_project_list_is_paginated_for_authenticated_user(self):
        response = {"items": [{"id": "project-1"}], "next_cursor": "project-1"}
        with (
            patch.object(projects, "firebase_available", return_value=True),
            patch.object(projects.project_cloud_service, "list_project_history", return_value=response) as list_history,
        ):
            result = projects.list_project_history_route(
                page_size=1,
                cursor=None,
                authenticated_user="owner-1",
            )

        self.assertEqual(result, response)
        list_history.assert_called_once_with("owner-1", 1, None)

    def test_analysis_history_hides_projects_not_owned_by_user(self):
        with (
            patch.object(projects, "firebase_available", return_value=True),
            patch.object(projects.project_cloud_service, "list_analysis_history", return_value=None),
        ):
            with self.assertRaises(HTTPException) as raised:
                projects.list_project_analysis_history_route(
                    project_id="other-users-project",
                    page_size=10,
                    cursor=None,
                    authenticated_user="owner-1",
                )

        self.assertEqual(raised.exception.status_code, 404)

    def test_analysis_details_remain_readable_when_local_source_expired(self):
        detail = {
            "id": "analysis-1",
            "files": [],
            "findings": [],
            "quality_scores": [],
        }
        with (
            patch.object(projects, "firebase_available", return_value=True),
            patch.object(projects.project_cloud_service, "get_analysis_history", return_value=detail),
            patch.object(projects.project_cloud_service, "project_source_storage", return_value="local"),
            patch.object(projects.project_upload_service, "source_files_available", return_value=False),
        ):
            result = projects.get_project_analysis_history_route(
                project_id="project-1",
                analysis_id="analysis-1",
                authenticated_user="owner-1",
            )

        self.assertFalse(result["source_available"])
        self.assertEqual(result["source_storage"], "expired")
        self.assertEqual(result["id"], "analysis-1")


if __name__ == "__main__":
    unittest.main()
