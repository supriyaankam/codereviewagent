import tempfile
import unittest
import io
from pathlib import Path
from unittest.mock import Mock, patch

from app.services import project_cloud_service as cloud


class ProjectCloudServiceTests(unittest.TestCase):
    def setUp(self):
        self.db = Mock()
        self.project_ref = Mock()
        self.db.collection.return_value.document.return_value = self.project_ref

    def test_upload_metadata_omits_source_contents(self):
        batch = self.db.batch.return_value
        with patch.object(cloud, "firebase_credentials_configured", return_value=True):
            with patch.object(cloud, "get_db", return_value=self.db):
                persisted = cloud.record_upload_created(
                    "upload-1",
                    "owner-1",
                    {
                        "name": "sample",
                        "created_at": 123.0,
                        "files": [{"path": "src/main.py", "size": 20}],
                    },
                )

        self.assertTrue(persisted)
        project_data = self.project_ref.set.call_args.args[0]
        self.assertEqual(project_data["owner_id"], "owner-1")
        self.assertEqual(project_data["file_count"], 1)
        self.assertNotIn("code", project_data)
        file_data = batch.set.call_args.args[1]
        self.assertEqual(file_data["path"], "src/main.py")
        self.assertNotIn("content", file_data)

    def test_analysis_history_stores_only_safe_finding_metadata(self):
        analysis_ref = Mock()
        findings_ref = Mock()
        files_ref = Mock()
        self.project_ref.collection.side_effect = lambda name: {
            "analysis_history": Mock(document=Mock(return_value=analysis_ref)),
            "files": files_ref,
        }[name]
        analysis_ref.collection.side_effect = lambda name: {
            "findings": findings_ref,
            "files": files_ref,
        }[name]
        findings_ref.stream.return_value = []
        files_ref.stream.return_value = []
        secret = "gsk_synthetic_secret_123456789"
        password = "supersecret-password-value"
        source_snippet = 'def private_handler():\n    return "source-code-sentinel"\n'

        with patch.object(cloud, "firebase_credentials_configured", return_value=True):
            with patch.object(cloud, "get_db", return_value=self.db):
                persisted = cloud.record_analysis(
                    "upload-1",
                    "analysis-1",
                    [{
                        "file_index": 0,
                        "path": "src/main.py",
                        "language": "python",
                        "summary": f"Credential found: GROQ_API_KEY = '{secret}'\n{source_snippet}",
                        "ai_available": True,
                        "source_sha256": "a" * 64,
                        "issues": [{
                            "line": 8,
                            "severity": "critical",
                            "category": "security",
                            "title": f"Hardcoded API key: {secret}",
                            "source": "pylint",
                            "description": f"Found {secret} in this source:\n{source_snippet}",
                            "recommendation": f"Replace {password} with this code:\n{source_snippet}",
                            "beginner_explanation": {
                                "problem": f"The source contains {secret}.",
                                "example": source_snippet,
                            },
                        }],
                    }],
                    {
                        "complete": True,
                        "analyzed": 1,
                        "selected": 1,
                        "skipped": 0,
                        "errors": [],
                        "languages": ["python"],
                    },
                )

        self.assertTrue(persisted)
        summary = analysis_ref.set.call_args.args[0]
        self.assertEqual(summary["issue_count"], 1)
        writes = self.db.batch.return_value.set.call_args_list
        file_result = writes[0].args[1]
        finding = writes[1].args[1]
        self.assertEqual(finding["path"], "src/main.py")
        self.assertEqual(finding["title"], "Security finding")
        self.assertEqual(finding["severity"], "critical")
        self.assertEqual(file_result["source_sha256"], "a" * 64)
        self.assertEqual(
            set(finding),
            {"file_index", "path", "line", "severity", "category", "title", "source", "confidence"},
        )
        self.assertNotIn("summary", file_result)
        self.assertNotIn("ai_failure_reason", file_result)
        persisted_payloads = [
            summary,
            file_result,
            finding,
            self.project_ref.set.call_args.args[0],
        ]
        serialized = repr(persisted_payloads)
        for value in (secret, password, source_snippet, "private_handler"):
            self.assertNotIn(value, serialized)
        self.assertNotIn("description", finding)
        self.assertNotIn("recommendation", finding)
        self.assertNotIn("beginner_explanation", finding)

    def test_quality_score_is_linked_to_analysis_run(self):
        source_snippet = "def score_source_sentinel(): pass"
        secret = "score-secret-sentinel"
        with patch.object(cloud, "firebase_credentials_configured", return_value=True):
            with patch.object(cloud, "get_db", return_value=self.db):
                persisted = cloud.record_quality_score(
                    "upload-1",
                    {
                        "score": 88,
                        "categories": {"security": 90, "source_code": source_snippet},
                        "metrics": {"nonblank_lines": 12, "source_code": source_snippet},
                        "coverage": {"analyzed": 2, "source_code": secret},
                        "factors": [source_snippet, secret],
                    },
                    "analysis-1",
                )

        self.assertTrue(persisted)
        score_data = self.project_ref.collection.return_value.document.return_value.set.call_args.args[0]
        self.assertEqual(score_data["analysis_id"], "analysis-1")
        self.assertEqual(score_data["categories"], {"security": 90})
        self.assertEqual(score_data["metrics"], {"nonblank_lines": 12})
        self.assertEqual(score_data["coverage"], {"analyzed": 2})
        self.assertNotIn(source_snippet, repr(score_data))
        self.assertNotIn(secret, repr(score_data))

    def test_project_history_returns_cursor_pages_of_uploaded_projects(self):
        documents = [
            Mock(id="project-3", to_dict=Mock(return_value={
                "owner_id": "owner-1", "name": "newest", "file_count": 3,
                "created_at": 30, "source_code": "project-code-sentinel",
            })),
            Mock(id="project-2", to_dict=Mock(return_value={
                "owner_id": "owner-1", "name": "middle", "file_count": 2, "created_at": 20,
            })),
            Mock(id="ordinary-project", to_dict=Mock(return_value={
                "owner_id": "owner-1", "name": "not an upload",
            })),
        ]
        query = self.db.collection.return_value.where.return_value
        query.stream.return_value = documents
        with patch.object(cloud, "get_db", return_value=self.db):
            first = cloud.list_project_history("owner-1", page_size=1)
            second = cloud.list_project_history("owner-1", page_size=1, cursor="project-3")

        self.assertEqual(first["items"][0]["id"], "project-3")
        self.assertNotIn("source_code", first["items"][0])
        self.assertNotIn("project-code-sentinel", repr(first["items"][0]))
        self.assertEqual(first["next_cursor"], "project-3")
        self.assertEqual(second["items"][0]["id"], "project-2")
        self.assertIsNone(second["next_cursor"])
        self.db.collection.return_value.where.assert_called_with("owner_id", "==", "owner-1")

    def test_analysis_history_is_owner_scoped_and_cursor_paginated(self):
        project_snapshot = Mock(
            exists=True,
            to_dict=Mock(return_value={"owner_id": "owner-1"}),
        )
        project_ref = Mock()
        project_ref.get.return_value = project_snapshot
        analysis_documents = [
            Mock(id="run-2", to_dict=Mock(return_value={
                "created_at": 20,
                "issue_count": 2,
                "summary": "legacy-summary-sentinel",
                "source_code": "legacy-list-source-sentinel",
            })),
            Mock(id="run-1", to_dict=Mock(return_value={"created_at": 10, "issue_count": 1})),
        ]
        project_ref.collection.return_value.stream.return_value = analysis_documents
        self.db.collection.return_value.document.return_value = project_ref

        with patch.object(cloud, "get_db", return_value=self.db):
            first = cloud.list_analysis_history("project-1", "owner-1", page_size=1)
            second = cloud.list_analysis_history(
                "project-1",
                "owner-1",
                page_size=1,
                cursor="run-2",
            )
            unauthorized = cloud.list_analysis_history("project-1", "owner-2", page_size=1)

        self.assertEqual(first["items"][0]["id"], "run-2")
        self.assertNotIn("summary", first["items"][0])
        self.assertNotIn("source_code", first["items"][0])
        self.assertNotIn("legacy-list-source-sentinel", repr(first["items"][0]))
        self.assertEqual(first["next_cursor"], "run-2")
        self.assertEqual(second["items"][0]["id"], "run-1")
        self.assertIsNone(second["next_cursor"])
        self.assertIsNone(unauthorized)

    def test_analysis_detail_accepts_legacy_runs_without_saved_file_results(self):
        secret = "legacy-secret-value-123456"
        source_snippet = 'def legacy_handler():\n    return "legacy-source-sentinel"\n'
        project_ref = Mock()
        project_ref.get.return_value = Mock(
            exists=True,
            to_dict=Mock(return_value={
                "owner_id": "owner-1",
                "name": "legacy",
                "storage_backend": "local",
            }),
        )
        analysis_ref = Mock()
        analysis_ref.get.return_value = Mock(
            exists=True,
            id="run-old",
            to_dict=Mock(return_value={
                "status": "complete",
                "issue_count": 1,
                "summary": f"{secret}\n{source_snippet}",
                "source_code": source_snippet,
            }),
        )
        findings_ref = Mock()
        findings_ref.stream.return_value = [
            Mock(id="0-0", to_dict=Mock(return_value={
                "path": "main.py",
                "line": 2,
                "title": "Old finding",
                "severity": "warning",
                "description": f"Contains {secret} and {source_snippet}",
                "recommendation": source_snippet,
                "beginner_explanation": {"example": source_snippet},
            })),
        ]
        files_ref = Mock()
        files_ref.stream.return_value = [
            Mock(id="0", to_dict=Mock(return_value={
                "file_index": 0,
                "path": "main.py",
                "language": "python",
                "summary": source_snippet,
                "source_code": source_snippet,
                "ai_failure_reason": secret,
            })),
        ]
        scores_ref = Mock()
        scores_ref.stream.return_value = [
            Mock(id="old-score", to_dict=Mock(return_value={
                "score": 88,
                "analysis_id": "run-old",
                "factors": [secret, source_snippet],
                "metrics": {"source_code": source_snippet, "nonblank_lines": 2},
            })),
        ]
        analysis_ref.collection.side_effect = lambda name: {
            "files": files_ref,
            "findings": findings_ref,
        }[name]
        project_ref.collection.side_effect = lambda name: {
            "analysis_history": Mock(document=Mock(return_value=analysis_ref)),
            "score_history": scores_ref,
        }[name]
        self.db.collection.return_value.document.return_value = project_ref

        with patch.object(cloud, "get_db", return_value=self.db):
            detail = cloud.get_analysis_history("project-1", "run-old", "owner-1")

        self.assertEqual(detail["files"][0]["path"], "main.py")
        self.assertNotIn("summary", detail["files"][0])
        self.assertNotIn("source_code", detail["files"][0])
        self.assertNotIn("ai_failure_reason", detail["files"][0])
        self.assertEqual(detail["findings"][0]["title"], "Quality finding")
        self.assertNotIn("description", detail["findings"][0])
        self.assertNotIn("recommendation", detail["findings"][0])
        self.assertNotIn("beginner_explanation", detail["findings"][0])
        self.assertNotIn("source_code", detail)
        self.assertEqual(detail["quality_scores"][0]["score"], 88)
        self.assertNotIn("factors", detail["quality_scores"][0])
        self.assertNotIn("source_code", detail["quality_scores"][0]["metrics"])
        self.assertNotIn(secret, repr(detail))
        self.assertNotIn(source_snippet, repr(detail))

    def test_uploaded_files_use_resumable_cloud_storage_chunks(self):
        bucket = Mock()
        blob = Mock()
        blob.exists.return_value = False
        bucket.blob.return_value = blob
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_dir = Path(temporary_directory)
            source = session_dir / "project" / "nested" / "main.py"
            source.parent.mkdir(parents=True)
            source.write_text("print('safe')\n", encoding="utf-8")
            with patch.object(cloud, "storage_configured", return_value=True):
                with patch.object(cloud.storage, "bucket", return_value=bucket):
                    with patch.object(cloud, "get_db", return_value=self.db):
                        stored = cloud.upload_project_files(
                            "upload-1",
                            "owner-1",
                            session_dir,
                            {"files": [{"path": "nested/main.py", "size": source.stat().st_size}]},
                        )

        self.assertTrue(stored)
        self.assertEqual(blob.chunk_size, 8 * 1024 * 1024)
        bucket.blob.assert_called_once_with(
            "users/owner-1/projects/upload-1/files/nested/main.py"
        )
        blob.upload_from_filename.assert_called_once()
        self.assertEqual(blob.upload_from_filename.call_args.kwargs["if_generation_match"], 0)

    def test_archive_uses_streaming_storage_upload(self):
        bucket = Mock()
        blob = Mock()
        blob.exists.return_value = False
        bucket.blob.return_value = blob
        archive = io.BytesIO(b"zip-data")
        with patch.object(cloud, "storage_configured", return_value=True):
            with patch.object(cloud.storage, "bucket", return_value=bucket):
                with patch.object(cloud, "get_db", return_value=self.db):
                    object_name = cloud.upload_project_archive(
                        "upload-1",
                        "owner-1",
                        archive,
                    )

        self.assertEqual(object_name, "users/owner-1/projects/upload-1/archive.zip")
        blob.upload_from_file.assert_called_once()
        self.assertEqual(blob.upload_from_file.call_args.kwargs["size"], len(b"zip-data"))
        self.assertTrue(blob.upload_from_file.call_args.kwargs["rewind"])


if __name__ == "__main__":
    unittest.main()
