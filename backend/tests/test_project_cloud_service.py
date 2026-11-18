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

    def test_analysis_history_stores_findings_without_source_code(self):
        analysis_ref = Mock()
        findings_ref = Mock()
        self.project_ref.collection.return_value.document.return_value = analysis_ref
        analysis_ref.collection.return_value = findings_ref
        findings_ref.stream.return_value = []

        with patch.object(cloud, "firebase_credentials_configured", return_value=True):
            with patch.object(cloud, "get_db", return_value=self.db):
                persisted = cloud.record_analysis(
                    "upload-1",
                    "analysis-1",
                    [{
                        "file_index": 0,
                        "path": "src/main.py",
                        "issues": [{
                            "line": 8,
                            "severity": "warning",
                            "category": "quality",
                            "title": "Avoid a mutable default",
                            "source": "pylint",
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
        finding = self.db.batch.return_value.set.call_args.args[1]
        self.assertEqual(finding["path"], "src/main.py")
        self.assertNotIn("code", finding)
        self.assertNotIn("source_code", finding)

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
