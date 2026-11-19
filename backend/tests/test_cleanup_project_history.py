import contextlib
import io
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from google.api_core.exceptions import Aborted

from scripts import cleanup_project_history as cleanup


class ProjectHistoryCleanupTests(unittest.TestCase):
    def test_sanitizers_preserve_safe_metadata_and_strip_legacy_ai_text(self):
        secret = "gsk_synthetic_history_secret_012345"
        password = "history-password-sentinel"
        snippet = 'def source_sentinel():\n    return "private-code-sentinel"\n'
        documents = {
            "project": {
                "id": "project-1",
                "name": "sample.zip",
                "owner_id": "owner-1",
                "status": "analyzed",
                "file_count": 1,
                "total_bytes": 64,
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "description": f"{secret} {snippet}",
                "source_code": snippet,
            },
            "project_file": {
                "path": "src/main.py",
                "size": 64,
                "status": "stored",
                "storage_path": "users/owner-1/projects/project-1/files/src/main.py",
                "content": snippet,
            },
            "analysis": {
                "status": "complete",
                "analyzed_files": 1,
                "issue_count": 1,
                "languages": ["python"],
                "summary": f"{secret} {snippet}",
                "recommendations": [password],
            },
            "analysis_file": {
                "file_index": 0,
                "path": "src/main.py",
                "language": "python",
                "source_sha256": "a" * 64,
                "summary": snippet,
                "source_code": snippet,
                "ai_failure_reason": secret,
            },
            "finding": {
                "file_index": 0,
                "path": "src/main.py",
                "line": 4,
                "severity": "high",
                "category": "security",
                "title": f"Key {secret}",
                "source": "bandit",
                "confidence": "high",
                "description": snippet,
                "recommendation": password,
                "beginner_explanation": {"example": snippet},
            },
            "score": {
                "score": 82,
                "categories": {"security": 70, "source_code": snippet},
                "metrics": {"nonblank_lines": 12, "source_code": snippet},
                "coverage": {"analyzed": 1},
                "analysis_id": "analysis-1",
                "factors": [secret, snippet],
            },
        }

        sanitized = {
            kind: cleanup.sanitize_document(kind, data)
            for kind, data in documents.items()
        }
        serialized = repr(sanitized)
        for unsafe_value in (secret, password, snippet, "source_sentinel", "private-code-sentinel"):
            self.assertNotIn(unsafe_value, serialized)
        self.assertEqual(sanitized["project"]["owner_id"], "owner-1")
        self.assertNotIn("description", sanitized["project"])
        self.assertEqual(sanitized["analysis"]["issue_count"], 1)
        self.assertEqual(sanitized["finding"]["title"], "Security finding")
        self.assertEqual(sanitized["finding"]["line"], 4)
        self.assertEqual(sanitized["score"]["score"], 82)
        self.assertEqual(sanitized["score"]["categories"], {"security": 70})
        self.assertEqual(sanitized["score"]["metrics"], {"nonblank_lines": 12})
        self.assertEqual(sanitized["score"]["coverage"], {"analyzed": 1})

    def test_secret_in_a_preserved_path_is_redacted(self):
        sanitized = cleanup.sanitize_document(
            "analysis_file",
            {
                "path": "https://user:synthetic-password@example.test/src/main.py",
                "file_index": 0,
            },
        )

        self.assertNotIn("synthetic-password", sanitized["path"])
        self.assertEqual(sanitized["file_index"], 0)

    def test_finding_title_normalization_preserves_all_other_safe_fields(self):
        finding = {
            "file_index": 3,
            "path": "src/auth.py",
            "line": 27,
            "severity": "warning",
            "category": "security",
            "title": "Legacy detailed finding title",
            "source": "bandit",
            "confidence": "high",
        }

        sanitized = cleanup.sanitize_document("finding", finding)
        removed, modified, added = cleanup._field_differences(finding, sanitized)

        self.assertEqual(
            set(sanitized),
            {
                "file_index", "path", "line", "severity", "category",
                "title", "source", "confidence",
            },
        )
        self.assertEqual(
            {key: sanitized[key] for key in finding if key != "title"},
            {key: value for key, value in finding.items() if key != "title"},
        )
        self.assertEqual(sanitized["title"], "Security finding")
        self.assertEqual(removed, {})
        self.assertEqual(modified, {"title": 1})
        self.assertEqual(added, {})

    def test_sanitization_is_idempotent_for_every_document_kind(self):
        documents = {
            "project": {
                "id": "project-id",
                "owner_id": "owner-id",
                "name": "sample.zip",
                "file_count": 1,
                "status": "uploaded",
                "description": "legacy description",
            },
            "project_file": {
                "path": "src/main.py",
                "size": 20,
                "status": "stored",
                "content": "legacy content",
            },
            "analysis": {
                "status": "complete",
                "analyzed_files": 1,
                "issue_count": 1,
                "languages": ["python"],
                "summary": "legacy summary",
            },
            "analysis_file": {
                "file_index": 0,
                "path": "src/main.py",
                "language": "python",
                "source_sha256": "a" * 64,
                "summary": "legacy file summary",
            },
            "finding": {
                "file_index": 0,
                "path": "src/main.py",
                "line": 7,
                "severity": "warning",
                "category": "security",
                "title": "Legacy detailed title",
                "source": "bandit",
                "confidence": "high",
                "description": "legacy explanation",
            },
            "score": {
                "score": 90,
                "categories": {"security": 90},
                "metrics": {"nonblank_lines": 12},
                "coverage": {"analyzed": 1},
                "factors": ["legacy factor"],
            },
        }

        for kind, original in documents.items():
            with self.subTest(kind=kind):
                once = cleanup.sanitize_document(kind, original)
                twice = cleanup.sanitize_document(kind, once)

                self.assertEqual(twice, once)
                removed, modified, added = cleanup._field_differences(once, twice)
                self.assertEqual(removed, {})
                self.assertEqual(modified, {})
                self.assertEqual(added, {})
        self.assertEqual(cleanup._project_record_scope(
            cleanup.sanitize_document("project", documents["project"])
        ), "eligible")

    def test_running_migration_twice_makes_no_second_pass_changes(self):
        class MemoryCollection:
            def __init__(self, documents):
                self.documents = documents

            def stream(self):
                return self.documents

        class MemoryDocument:
            def __init__(self, data, collections=None):
                self.data = data
                self.collections = collections or {}
                self.reference = self
                self.update_time = 1

            def to_dict(self):
                return dict(self.data)

            @property
            def exists(self):
                return True

            def get(self, transaction=None):
                return self

            def set(self, data):
                self.data = dict(data)
                self.update_time += 1

            def collection(self, name):
                return self.collections[name]

        class MemoryTransaction:
            def __init__(self):
                self.pending = []

            def set(self, reference, data):
                self.pending.append((reference, data))

            def commit(self):
                for reference, data in self.pending:
                    reference.set(data)

            def rollback(self):
                self.pending.clear()

        finding = MemoryDocument({
            "file_index": 0,
            "path": "src/main.py",
            "line": 8,
            "severity": "warning",
            "category": "security",
            "title": "Legacy detailed title",
            "source": "bandit",
            "confidence": "high",
        })
        analysis = MemoryDocument(
            {"status": "complete"},
            {
                "files": MemoryCollection([]),
                "findings": MemoryCollection([finding]),
            },
        )
        score = MemoryDocument({
            "score": 82,
            "categories": {"security": 70, "source_code": "synthetic nested payload"},
        })
        project = MemoryDocument(
            {
                "id": "project-id",
                "owner_id": "owner-id",
                "file_count": 1,
                "status": "uploaded",
                "description": "legacy description",
            },
            {
                "files": MemoryCollection([]),
                "analysis_history": MemoryCollection([analysis]),
                "score_history": MemoryCollection([score]),
            },
        )
        db = Mock()
        db.collection.return_value = MemoryCollection([project])
        db.transaction.side_effect = MemoryTransaction

        first_pass = cleanup.migrate_project_history(db, apply=True)
        second_pass = cleanup.migrate_project_history(db, apply=True)

        self.assertEqual(first_pass["documents_changed"]["project"], 1)
        self.assertEqual(first_pass["documents_changed"]["finding"], 1)
        self.assertEqual(first_pass["documents_changed"]["score"], 1)
        self.assertEqual(score.data["categories"], {"security": 70})
        self.assertEqual(sum(second_pass["documents_changed"].values()), 0)
        self.assertEqual(sum(second_pass["fields_removed"].values()), 0)
        self.assertEqual(sum(second_pass["fields_modified"].values()), 0)
        self.assertEqual(sum(second_pass["fields_added"].values()), 0)
        self.assertEqual(sum(second_pass["write_conflicts"].values()), 0)

    def test_dry_run_counts_unsafe_fields_without_writing(self):
        secret = "dry-run-secret-sentinel"
        project_ref, project, refs = self._project_fixture(
            project_data={
                "id": "project-1",
                "owner_id": "owner-1",
                "status": "uploaded",
                "file_count": 1,
                "description": secret,
            },
            finding_data={"category": "security", "description": secret},
        )
        db = Mock()
        db.collection.return_value.stream.return_value = [project]

        stats = cleanup.migrate_project_history(db)

        self.assertEqual(stats["documents_scanned"]["project"], 1)
        self.assertEqual(stats["documents_changed"]["project"], 1)
        self.assertEqual(stats["fields_removed"]["project"], 1)
        self.assertEqual(stats["removed_fields_by_name"]["project"], {"description": 1})
        self.assertEqual(stats["added_fields_by_name"]["finding"], {"title": 1})
        self.assertEqual(stats["fields_removed"]["finding"], 1)
        self.assertEqual(stats["eligible_history_uploads"], 1)
        self.assertEqual(stats["documents_written"]["project"], 0)
        db.transaction.assert_not_called()

    def test_dry_run_skips_ordinary_projects_and_all_subcollections(self):
        project_ref, project, refs = self._project_fixture(
            project_data={
                "id": "ordinary-project-id",
                "owner_id": "ordinary-owner-id",
                "name": "ordinary project",
                "description": "Keep this user-entered project description.",
            },
            finding_data={"category": "security", "title": "Keep history untouched"},
        )
        db = Mock()
        db.collection.return_value.stream.return_value = [project]
        output = io.StringIO()

        stats = cleanup.migrate_project_history(db)
        with contextlib.redirect_stdout(output):
            cleanup._print_report(stats, apply=False)

        self.assertEqual(stats["ordinary_projects_skipped"], 1)
        self.assertEqual(stats["ambiguous_history_records_skipped"], 0)
        self.assertEqual(stats["eligible_history_uploads"], 0)
        self.assertEqual(stats["documents_scanned"]["project"], 0)
        self.assertEqual(stats["documents_changed"]["project"], 0)
        self.assertEqual(stats["documents_scanned"]["finding"], 0)
        self.assertEqual(stats["fields_removed"]["project"], 0)
        project_ref.update.assert_not_called()
        project_ref.collection.assert_not_called()
        self.assertIn("Eligible Project History uploads: 0.", output.getvalue())
        self.assertIn("Ordinary projects skipped: 1.", output.getvalue())
        self.assertIn("Ambiguous or partial history records skipped: 0.", output.getvalue())
        self.assertNotIn("ordinary-project-id", output.getvalue())
        self.assertNotIn("ordinary-owner-id", output.getvalue())
        self.assertNotIn("Keep this user-entered project description.", output.getvalue())
        for ref in refs.values():
            ref.update.assert_not_called()

    def test_invalid_file_count_does_not_qualify_as_upload_marker(self):
        project_ref, project, _ = self._project_fixture(
            project_data={
                "id": "ordinary-project-id",
                "owner_id": "ordinary-owner-id",
                "file_count": "3",
                "description": "Keep this description.",
            },
            finding_data={},
        )
        db = Mock()
        db.collection.return_value.stream.return_value = [project]

        stats = cleanup.migrate_project_history(db)

        self.assertEqual(stats["ordinary_projects_skipped"], 0)
        self.assertEqual(stats["ambiguous_history_records_skipped"], 1)
        self.assertEqual(stats["documents_scanned"]["project"], 0)
        project_ref.update.assert_not_called()
        project_ref.collection.assert_not_called()

    def test_partial_upload_after_creation_write_failure_is_skipped(self):
        project_ref, project, refs = self._project_fixture(
            project_data={
                "id": "partial-upload-id",
                "owner_id": "upload-owner-id",
                "name": "partial upload",
                "description": "May contain useful or sensitive user text.",
                "status": "uploaded",
                "storage_backend": "local",
            },
            finding_data={"category": "security", "title": "Keep subcollection intact"},
        )
        db = Mock()
        db.collection.return_value.stream.return_value = [project]
        transaction = Mock()
        db.transaction.return_value = transaction
        output = io.StringIO()

        stats = cleanup.migrate_project_history(db)
        with contextlib.redirect_stdout(output):
            cleanup._print_report(stats, apply=False)

        self.assertEqual(stats["eligible_history_uploads"], 0)
        self.assertEqual(stats["ordinary_projects_skipped"], 0)
        self.assertEqual(stats["ambiguous_history_records_skipped"], 1)
        self.assertEqual(stats["documents_scanned"]["project"], 0)
        self.assertEqual(stats["documents_scanned"]["finding"], 0)
        project_ref.update.assert_not_called()
        project_ref.collection.assert_not_called()
        self.assertIn("Ambiguous or partial history records skipped: 1.", output.getvalue())
        self.assertNotIn("partial-upload-id", output.getvalue())
        self.assertNotIn("upload-owner-id", output.getvalue())
        self.assertNotIn("May contain useful or sensitive user text.", output.getvalue())
        for ref in refs.values():
            ref.update.assert_not_called()

    def test_partial_failure_record_with_upload_status_is_not_treated_as_ordinary(self):
        project_ref, project, _ = self._project_fixture(
            project_data={
                "id": "failed-partial-upload-id",
                "owner_id": "upload-owner-id",
                "status": "failed",
                "updated_at": 123.0,
            },
            finding_data={},
        )
        db = Mock()
        db.collection.return_value.stream.return_value = [project]

        stats = cleanup.migrate_project_history(db)

        self.assertEqual(stats["eligible_history_uploads"], 0)
        self.assertEqual(stats["ordinary_projects_skipped"], 0)
        self.assertEqual(stats["ambiguous_history_records_skipped"], 1)
        project_ref.update.assert_not_called()
        project_ref.collection.assert_not_called()

    def test_dry_run_reports_why_920_findings_need_changes_without_values(self):
        finding_documents = [
            {
                "file_index": 0,
                "path": "src/main.py",
                "line": line,
                "severity": "warning",
                "category": "security",
                "title": "Legacy detailed finding title",
                "source": "bandit",
                "confidence": "high",
            }
            for line in range(1, 921)
        ]
        _, project, _ = self._project_fixture(
            project_data={
                "id": "synthetic-project-id",
                "owner_id": "synthetic-owner-id",
                "status": "uploaded",
                "file_count": 1,
            },
            finding_data=finding_documents,
        )
        db = Mock()
        db.collection.return_value.stream.return_value = [project]
        output = io.StringIO()

        stats = cleanup.migrate_project_history(db)
        with contextlib.redirect_stdout(output):
            cleanup._print_report(stats, apply=False)

        self.assertEqual(stats["documents_scanned"]["finding"], 920)
        self.assertEqual(stats["documents_changed"]["finding"], 920)
        self.assertEqual(stats["modified_fields_by_name"]["finding"], {"title": 920})
        self.assertEqual(stats["fields_removed"]["finding"], 0)
        self.assertEqual(stats["documents_written"]["finding"], 0)
        self.assertEqual(stats["ordinary_projects_skipped"], 0)
        self.assertEqual(stats["ambiguous_history_records_skipped"], 0)
        self.assertEqual(stats["eligible_history_uploads"], 1)
        report = output.getvalue()
        self.assertIn("findings: 920 scanned, 920 needing changes.", report)
        self.assertIn("title=920", report)
        self.assertIn("Anonymized finding example (illustrative only", report)
        self.assertNotIn("synthetic-project-id", report)
        self.assertNotIn("synthetic-owner-id", report)
        self.assertNotIn("Legacy detailed finding title", report)

        safe = cleanup.sanitize_document("finding", finding_documents[0])
        for field in ("severity", "category", "path", "line", "file_index", "source", "confidence"):
            self.assertEqual(safe[field], finding_documents[0][field])

    def test_apply_rewrites_only_allowlisted_documents_and_logs_no_values(self):
        secret = "apply-secret-sentinel"
        snippet = "def apply_source_sentinel(): pass"
        project_ref, project, refs = self._project_fixture(
            project_data={
                "id": "project-1",
                "owner_id": "owner-1",
                "status": "analyzed",
                "file_count": 1,
                "source_code": snippet,
            },
            finding_data={
                "category": "security",
                "line": 3,
                "title": secret,
                "recommendation": snippet,
            },
            score_data={
                "score": 91,
                "categories": {"security": 90},
                "factors": [secret, snippet],
            },
            analysis_data={
                "status": "complete",
                "analyzed_files": 1,
                "summary": secret,
            },
            project_file_data={
                "path": "src/main.py",
                "size": 20,
                "status": "stored",
                "source_code": snippet,
            },
            analysis_file_data={
                "file_index": 0,
                "path": "src/main.py",
                "language": "python",
                "summary": secret,
            },
        )
        db = Mock()
        db.collection.return_value.stream.return_value = [project]
        transaction = Mock()
        db.transaction.return_value = transaction
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            stats = cleanup.migrate_project_history(db, apply=True)
            cleanup._print_report(stats, apply=True)

        expected_documents = [
            (
                project_ref,
                {
                    "id": "project-1",
                    "owner_id": "owner-1",
                    "status": "analyzed",
                    "file_count": 1,
                },
            ),
            (
                refs["project_file"],
                {"path": "src/main.py", "size": 20, "status": "stored"},
            ),
            (
                refs["analysis"],
                {"status": "complete", "analyzed_files": 1},
            ),
            (
                refs["analysis_file"],
                {"file_index": 0, "path": "src/main.py", "language": "python"},
            ),
            (
                refs["finding"],
                {"category": "security", "title": "Security finding", "line": 3},
            ),
            (
                refs["score"],
                {"score": 91, "categories": {"security": 90}},
            ),
        ]
        self.assertEqual(
            [call.args[0] for call in transaction.set.call_args_list],
            [reference for reference, _ in expected_documents],
        )
        self.assertEqual(
            [call.args[1] for call in transaction.set.call_args_list],
            [data for _, data in expected_documents],
        )
        self.assertEqual(stats["documents_written"]["project"], 1)
        self.assertEqual(stats["documents_written"]["analysis"], 1)
        self.assertEqual(stats["documents_written"]["finding"], 1)
        self.assertEqual(stats["documents_written"]["score"], 1)
        self.assertEqual(stats["documents_written"]["project_file"], 1)
        self.assertEqual(stats["documents_written"]["analysis_file"], 1)
        for ref in refs.values():
            ref.delete.assert_not_called()
        written_documents = repr(transaction.set.call_args_list)
        self.assertNotIn(secret, output.getvalue())
        self.assertNotIn(snippet, output.getvalue())
        self.assertNotIn(secret, written_documents)
        self.assertNotIn(snippet, written_documents)

    def test_changed_document_is_skipped_before_transactional_replace(self):
        project_ref, project, _ = self._project_fixture(
            project_data={
                "id": "project-1",
                "owner_id": "owner-1",
                "file_count": 1,
                "status": "uploaded",
                "description": "legacy description",
            },
            finding_data={},
        )
        project_ref.get.return_value = Mock(
            exists=True,
            update_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        db = Mock()
        db.collection.return_value.stream.return_value = [project]
        transaction = Mock()
        db.transaction.return_value = transaction
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            stats = cleanup.migrate_project_history(db, apply=True)
            cleanup._print_report(stats, apply=True)

        self.assertEqual(stats["write_conflicts"]["project"], 1)
        self.assertEqual(stats["documents_written"]["project"], 0)
        self.assertEqual(stats["documents_changed"]["project"], 1)
        transaction.rollback.assert_called_once()
        transaction.set.assert_not_called()
        self.assertIn("Write conflicts skipped: 1.", output.getvalue())
        project_ref.collection.assert_not_called()

    def test_concurrent_transaction_abort_is_counted_and_skipped(self):
        project_ref, project, _ = self._project_fixture(
            project_data={
                "id": "project-1",
                "owner_id": "owner-1",
                "file_count": 1,
                "status": "uploaded",
                "description": "legacy description",
            },
            finding_data={},
        )
        db = Mock()
        db.collection.return_value.stream.return_value = [project]
        transaction = Mock()
        transaction.commit.side_effect = Aborted("synthetic transaction conflict")
        db.transaction.return_value = transaction

        stats = cleanup.migrate_project_history(db, apply=True)

        self.assertEqual(stats["write_conflicts"]["project"], 1)
        self.assertEqual(stats["documents_written"]["project"], 0)
        transaction.set.assert_called_once()
        transaction.commit.assert_called_once()
        project_ref.collection.assert_not_called()

    def test_non_conflict_write_failure_is_raised(self):
        project_ref, project, _ = self._project_fixture(
            project_data={
                "id": "project-1",
                "owner_id": "owner-1",
                "file_count": 1,
                "status": "uploaded",
                "description": "legacy description",
            },
            finding_data={},
        )
        db = Mock()
        db.collection.return_value.stream.return_value = [project]
        transaction = Mock()
        transaction.commit.side_effect = RuntimeError("synthetic write failure")
        db.transaction.return_value = transaction

        with self.assertRaisesRegex(RuntimeError, "synthetic write failure"):
            cleanup.migrate_project_history(db, apply=True)

        transaction.set.assert_called_once()
        transaction.commit.assert_called_once()
        self.assertEqual(cleanup._initial_stats()["write_conflicts"]["project"], 0)

    def test_apply_flag_is_opt_in(self):
        self.assertFalse(cleanup._build_parser().parse_args([]).apply)
        self.assertTrue(cleanup._build_parser().parse_args(["--apply"]).apply)

    @staticmethod
    def _project_fixture(
        project_data,
        finding_data,
        score_data=None,
        analysis_data=None,
        project_file_data=None,
        analysis_file_data=None,
    ):
        project_ref = Mock()
        project = Mock(reference=project_ref)
        project.to_dict.return_value = project_data

        analysis_ref = Mock()
        analysis = Mock(reference=analysis_ref)
        analysis.to_dict.return_value = analysis_data or {"status": "complete"}
        finding_ref = Mock()
        finding_data_items = (
            finding_data if isinstance(finding_data, list)
            else [finding_data] if finding_data else []
        )
        finding = [
            Mock(reference=Mock(), to_dict=Mock(return_value=item))
            for item in finding_data_items
        ]
        findings_collection = Mock()
        findings_collection.stream.return_value = finding
        analysis_file_ref = Mock()
        analysis_file = Mock(reference=analysis_file_ref)
        analysis_file.to_dict.return_value = analysis_file_data or {}
        analysis_files_collection = Mock()
        analysis_files_collection.stream.return_value = [analysis_file] if analysis_file_data else []
        analysis_ref.collection.side_effect = lambda name: {
            "files": analysis_files_collection,
            "findings": findings_collection,
        }[name]
        project_file_ref = Mock()
        project_file = Mock(reference=project_file_ref)
        project_file.to_dict.return_value = project_file_data or {}
        project_files_collection = Mock()
        project_files_collection.stream.return_value = [project_file] if project_file_data else []
        analyses_collection = Mock()
        analyses_collection.stream.return_value = [analysis]
        scores_collection = Mock()
        scores_collection.stream.return_value = []
        project_collections = {
            "files": project_files_collection,
            "analysis_history": analyses_collection,
            "score_history": scores_collection,
        }
        project_ref.collection.side_effect = project_collections.__getitem__

        score_ref = Mock()
        if score_data:
            score = Mock(reference=score_ref)
            score.to_dict.return_value = score_data
            scores_collection.stream.return_value = [score]
        else:
            score = None

        documents = [
            (project, project_ref),
            (analysis, analysis_ref),
            (project_file, project_file_ref),
            (analysis_file, analysis_file_ref),
            *((item, item.reference) for item in finding),
        ]
        if score is not None:
            documents.append((score, score_ref))
        for document, reference in documents:
            document.update_time = object()
            reference.get.return_value = Mock(
                exists=True,
                update_time=document.update_time,
            )
        return project_ref, project, {
            "project": project_ref,
            "project_file": project_file_ref,
            "analysis": analysis_ref,
            "analysis_file": analysis_file_ref,
            "finding": finding[0].reference if finding else finding_ref,
            "score": score_ref,
        }


if __name__ == "__main__":
    unittest.main()
