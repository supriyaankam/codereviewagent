import asyncio
import hashlib
import io
import json
import os
import tempfile
import zipfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.routers.fix import require_project_upload_user
from app.services import project_cloud_service
from app.services import project_upload_service as uploads
from app.services.upload_service import EXTENSION_MAP, read_zip_archive


class ProjectUploadTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.environment = patch.dict(os.environ, {
            "PROJECT_UPLOAD_TEMP_DIR": self.temporary_directory.name,
            "PROJECT_UPLOAD_CHUNK_BYTES": str(8 * 1024 * 1024),
            "MAX_PROJECT_UPLOAD_BYTES": str(1024 * 1024 * 1024),
            "PROJECT_UPLOAD_TTL_HOURS": "24",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.chunk_size_patch = patch.object(uploads, "chunk_bytes", return_value=4)
        self.chunk_size_patch.start()
        self.addCleanup(self.chunk_size_patch.stop)
        cloud_patches = (
            patch.object(project_cloud_service, "record_upload_created", return_value=False),
            patch.object(project_cloud_service, "upload_project_files", return_value=False),
            patch.object(project_cloud_service, "upload_project_archive", return_value=None),
            patch.object(project_cloud_service, "record_upload_complete", return_value=False),
            patch.object(project_cloud_service, "record_upload_failure", return_value=False),
            patch.object(project_cloud_service, "record_analysis", return_value=False),
            patch.object(project_cloud_service, "record_quality_score", return_value=False),
            patch.object(project_cloud_service, "delete_project_objects"),
        )
        for cloud_patch in cloud_patches:
            cloud_patch.start()
            self.addCleanup(cloud_patch.stop)

    def create(self, files):
        return uploads.create_upload("user-1", "demo", files)

    def put_chunk(self, upload_id, file_index, chunk_index, content):
        return uploads.save_chunk(
            upload_id,
            "user-1",
            file_index,
            chunk_index,
            content,
            hashlib.sha256(content).hexdigest(),
        )

    def put_file(self, upload_id, file_index, content):
        size = uploads.chunk_bytes()
        for chunk_index, start in enumerate(range(0, len(content), size)):
            self.put_chunk(
                upload_id,
                file_index,
                chunk_index,
                content[start:start + size],
            )

    def create_analyzed_project_file(self, code, issue, path="src/main.py"):
        source = code.encode("utf-8")
        session = self.create([{"path": path, "size": len(source)}])
        session_dir = Path(self.temporary_directory.name) / session["upload_id"]
        source_path = session_dir / "project" / path
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(source)
        uploads.complete_upload(session["upload_id"], "user-1")
        uploads._write_state(session_dir, "analysis.json", {
            "selected_indices": [0],
            "processed_indices": [0],
            "complete": True,
            "results": {
                "0": {
                    "file_index": 0,
                    "path": path,
                    "language": "python",
                    "issues": [issue],
                    "static_issues": [issue],
                    "ai_issues": [],
                    "source_sha256": hashlib.sha256(source).hexdigest(),
                },
            },
        })
        return session, session_dir, source_path, source

    def test_nested_files_resume_duplicate_and_finalize(self):
        session = self.create([{"path": "nlp_project/src/model.py", "size": 8}])
        upload_id = session["upload_id"]

        first = self.put_chunk(upload_id, 0, 0, b"abcd")
        repeated = self.put_chunk(upload_id, 0, 0, b"abcd")
        self.assertFalse(first["duplicate"])
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(uploads.upload_status(upload_id, "user-1")["files"][0]["received_chunks"], [0])
        with self.assertRaises(uploads.ProjectUploadError) as missing_chunks:
            uploads.complete_upload(upload_id, "user-1")
        self.assertEqual(missing_chunks.exception.status_code, 409)

        self.put_chunk(upload_id, 0, 1, b"efgh")
        self.assertTrue(uploads.complete_upload(upload_id, "user-1")["complete"])
        stored_file = Path(self.temporary_directory.name) / upload_id / "project" / "nlp_project" / "src" / "model.py"
        self.assertEqual(stored_file.read_bytes(), b"abcdefgh")
        files = uploads.project_files(upload_id, "user-1")
        self.assertEqual(files["files"][0]["path"], "nlp_project/src/model.py")
        self.assertTrue(files["files"][0]["selectable"])

    def test_conflicting_duplicate_and_bad_checksum_are_rejected(self):
        session = self.create([{"path": "main.py", "size": 4}])
        upload_id = session["upload_id"]
        self.put_chunk(upload_id, 0, 0, b"good")

        with self.assertRaises(uploads.ProjectUploadError) as conflict:
            self.put_chunk(upload_id, 0, 0, b"nope")
        self.assertEqual(conflict.exception.status_code, 409)

        second = self.create([{"path": "second.py", "size": 4}])
        with self.assertRaises(uploads.ProjectUploadError) as checksum:
            uploads.save_chunk(second["upload_id"], "user-1", 0, 0, b"data", "0" * 64)
        self.assertIn("checksum", checksum.exception.detail.lower())

    def test_unsafe_and_duplicate_paths_are_rejected(self):
        for unsafe_path in ("../escape.py", "/root.py", "C:/root.py", ".", "folder/"):
            with self.subTest(path=unsafe_path), self.assertRaises(uploads.ProjectUploadError):
                self.create([{"path": unsafe_path, "size": 0}])

        with self.assertRaises(uploads.ProjectUploadError):
            self.create([
                {"path": "src/Model.py", "size": 0},
                {"path": "src/model.py", "size": 0},
            ])
        with self.assertRaises(uploads.ProjectUploadError):
            self.create([
                {"path": "src", "size": 0},
                {"path": "src/model.py", "size": 0},
            ])

    def test_legacy_zip_upload_rejects_traversal_paths(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("../outside.py", "print('unsafe')")
        with self.assertRaisesRegex(ValueError, "unsafe path"):
            read_zip_archive(archive.getvalue())

    def test_legacy_zip_reader_accepts_file_stream_and_increased_limits(self):
        from app.services.upload_service import (
            MAX_FILE_BYTES,
            MAX_FILE_IN_ZIP,
            MAX_TOTAL_BYTES,
            MAX_ZIP_BYTES,
        )

        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("src/main.py", "print('safe')\n")
        archive.seek(0)

        files = read_zip_archive(archive)
        self.assertEqual([source.path for source in files], ["src/main.py"])
        self.assertEqual(MAX_FILE_BYTES, 50 * 1024 * 1024)
        self.assertEqual(MAX_FILE_IN_ZIP, 20 * 1024 * 1024)
        self.assertEqual(MAX_TOTAL_BYTES, 500 * 1024 * 1024)
        self.assertEqual(MAX_ZIP_BYTES, 500 * 1024 * 1024)

    def test_configured_maximum_rejects_oversized_manifest(self):
        with patch.dict(os.environ, {"MAX_PROJECT_UPLOAD_BYTES": "4"}):
            with self.assertRaises(uploads.ProjectUploadError) as too_large:
                self.create([{"path": "large.py", "size": 5}])
        self.assertEqual(too_large.exception.status_code, 413)

    def test_400_mib_project_is_within_default_target_ceiling(self):
        project_size = 400 * 1024 * 1024
        with patch.dict(os.environ, {"MAX_PROJECT_UPLOAD_BYTES": str(512 * 1024 * 1024)}):
            with patch.object(uploads, "chunk_bytes", return_value=8 * 1024 * 1024):
                with patch.object(uploads.shutil, "disk_usage", return_value=SimpleNamespace(free=2 * 1024**3)):
                    session = self.create([{"path": "nlp_project/model.py", "size": project_size}])
        self.assertEqual(session["total_bytes"], project_size)
        self.assertFalse(session["complete"])

    def test_increased_analysis_limits_are_configurable_within_hard_caps(self):
        with patch.dict(os.environ, {
            "PROJECT_MAX_ANALYSIS_FILES": "200",
            "PROJECT_MAX_ANALYSIS_REQUEST_FILES": "20",
            "PROJECT_MAX_ANALYSIS_BATCH_BYTES": str(64 * 1024 * 1024),
            "PROJECT_MAX_ANALYSIS_FILE_BYTES": str(20 * 1024 * 1024),
        }):
            self.assertEqual(uploads.analysis_limits(), {
                "maximum_files": 200,
                "maximum_request_files": 20,
                "maximum_batch_bytes": 64 * 1024 * 1024,
                "maximum_file_bytes": 20 * 1024 * 1024,
            })

    def test_disk_space_and_expiration_are_reported(self):
        with patch.object(uploads.shutil, "disk_usage", return_value=SimpleNamespace(free=0)):
            with self.assertRaises(uploads.ProjectUploadError) as no_space:
                self.create([{"path": "model.py", "size": 4}])
        self.assertEqual(no_space.exception.status_code, 507)

        session = self.create([{"path": "model.py", "size": 0}])
        session_dir = Path(self.temporary_directory.name) / session["upload_id"]
        manifest_path = session_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["expires_at"] = 0
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(uploads.ProjectUploadError) as expired:
            uploads.upload_status(session["upload_id"], "user-1")
        self.assertEqual(expired.exception.status_code, 410)
        self.assertFalse(session_dir.exists())

    def test_assembly_disk_space_failure_can_resume_after_retry(self):
        session = self.create([{"path": "model.py", "size": 4}])
        content = b"data"
        with patch.object(uploads.shutil, "disk_usage", return_value=SimpleNamespace(free=0)):
            with self.assertRaises(uploads.ProjectUploadError) as no_space:
                self.put_chunk(session["upload_id"], 0, 0, content)
        self.assertEqual(no_space.exception.status_code, 507)
        with patch.object(uploads.shutil, "disk_usage", return_value=SimpleNamespace(free=0)):
            with self.assertRaises(uploads.ProjectUploadError):
                uploads.complete_upload(session["upload_id"], "user-1")

        with patch.object(uploads.shutil, "disk_usage", return_value=SimpleNamespace(free=1024)):
            retried = uploads.complete_upload(session["upload_id"], "user-1")
        self.assertTrue(retried["complete"])

    def test_configured_skip_directory_is_not_analyzable(self):
        with patch.dict(os.environ, {"PROJECT_UPLOAD_SKIP_DIRS": "generated, vendor"}):
            session = self.create([{"path": "generated/model.py", "size": 0}])
            uploads.complete_upload(session["upload_id"], "user-1")
            files = uploads.project_files(session["upload_id"], "user-1")
        self.assertFalse(files["files"][0]["selectable"])
        self.assertIn("Generated", files["files"][0]["skip_reason"])

    def test_analysis_is_bounded_to_twenty_files_per_request(self):
        manifest = [{"path": f"src/file_{index}.py", "size": 0} for index in range(21)]
        session = self.create(manifest)
        uploads.complete_upload(session["upload_id"], "user-1")

        with self.assertRaises(uploads.ProjectUploadError) as too_many:
            uploads.analyze_project_files(
                session["upload_id"],
                "user-1",
                list(range(21)),
                reset=True,
                selected_file_indices=list(range(21)),
            )
        self.assertIn("20 files per request", too_many.exception.detail)

    def test_other_user_cannot_access_session(self):
        session = self.create([{"path": "private.py", "size": 0}])

        with self.assertRaises(uploads.ProjectUploadError) as unauthorized:
            uploads.upload_status(session["upload_id"], "user-2")
        self.assertEqual(unauthorized.exception.status_code, 404)

    def test_unsupported_and_binary_files_are_not_selectable(self):
        session = self.create([
            {"path": "README.md", "size": 3},
            {"path": "assets/image.py", "size": 4},
        ])
        self.put_chunk(session["upload_id"], 0, 0, b"doc")
        self.put_chunk(session["upload_id"], 1, 0, b"\x00\x01\x02\x03")
        uploads.complete_upload(session["upload_id"], "user-1")
        files = uploads.project_files(session["upload_id"], "user-1")["files"]

        self.assertFalse(files[0]["selectable"])
        self.assertIn("Unsupported", files[0]["skip_reason"])
        self.assertFalse(files[1]["selectable"])
        self.assertIn("Binary", files[1]["skip_reason"])

    def test_project_source_extensions_cover_supported_language_variants(self):
        for extension, language in {
            ".pyi": "python",
            ".mjs": "javascript",
            ".cjs": "javascript",
            ".mts": "typescript",
            ".cts": "typescript",
            ".cc": "cpp",
            ".cxx": "cpp",
            ".c++": "cpp",
            ".hpp": "cpp",
            ".hh": "cpp",
            ".hxx": "cpp",
            ".kts": "kotlin",
        }.items():
            with self.subTest(extension=extension):
                self.assertEqual(EXTENSION_MAP[extension], language)

    def test_nested_mixed_language_project_uses_each_file_parser_and_scores_findings(self):
        sources = {
            "frontend/src/calc.mjs": (
                "function divide(value) {\n  return value / 0;\n}\n"
            ).encode(),
            "backend/math.py": (
                "def divide(value):\n    return value / 0\n"
            ).encode(),
            "native/src/math.cxx": (
                "double divide(double value) {\n  return value / 0;\n}\n"
            ).encode(),
            "README.md": b"not source",
            "build/generated.js": b"function generated(value) { return value / 0; }\n",
        }
        session = self.create([
            {"path": path, "size": len(content)}
            for path, content in sources.items()
        ])
        for index, content in enumerate(sources.values()):
            self.put_file(session["upload_id"], index, content)
        uploads.complete_upload(session["upload_id"], "user-1")

        files = uploads.project_files(session["upload_id"], "user-1")["files"]
        self.assertEqual(
            [files[index]["language"] for index in range(3)],
            ["javascript", "python", "cpp"],
        )
        self.assertFalse(files[3]["selectable"])
        self.assertIn("Unsupported", files[3]["skip_reason"])
        self.assertFalse(files[4]["selectable"])
        self.assertIn("Generated", files[4]["skip_reason"])

        with patch(
            "app.services.ai_reviewer.run_ai_review",
            return_value={"issues": [], "ai_available": False, "summary": ""},
        ) as ai_review:
            result = uploads.analyze_project_files(
                session["upload_id"],
                "user-1",
                [0, 1, 2],
                reset=True,
                selected_file_indices=[0, 1, 2],
            )
        findings_by_path = {
            item["path"]: [
                issue for issue in item["static_issues"]
                if issue["title"] == "Division or remainder by zero literal"
            ]
            for item in result["results"]
        }

        self.assertEqual(ai_review.call_count, 3)
        self.assertEqual(result["summary"]["languages"], ["cpp", "javascript", "python"])
        self.assertEqual(result["summary"]["analyzed"], 3)
        for path in list(sources)[:3]:
            with self.subTest(path=path):
                self.assertEqual(len(findings_by_path[path]), 1)
                self.assertEqual(findings_by_path[path][0]["line"], 2)

        score = uploads.project_quality_score(session["upload_id"], "user-1")
        self.assertEqual(score["metrics"]["analyzed_files"], 3)
        self.assertLess(score["score"], 100)
        self.assertEqual(
            {(item["path"], item["language"]) for item in score["files"]},
            {
                ("frontend/src/calc.mjs", "javascript"),
                ("backend/math.py", "python"),
                ("native/src/math.cxx", "cpp"),
            },
        )

    def test_project_upload_scores_constant_zero_divisor_expression(self):
        source = b"def divide(value):\n    return value / (2 - 2)\n"
        session = self.create([{"path": "nested/math.py", "size": len(source)}])
        self.put_file(session["upload_id"], 0, source)
        uploads.complete_upload(session["upload_id"], "user-1")

        with patch(
            "app.services.ai_reviewer.run_ai_review",
            return_value={"issues": [], "ai_available": False, "summary": ""},
        ):
            result = uploads.analyze_project_files(
                session["upload_id"],
                "user-1",
                [0],
                reset=True,
                selected_file_indices=[0],
            )

        findings = [
            issue for issue in result["results"][0]["static_issues"]
            if issue["title"] == "Division or remainder by constant zero expression"
        ]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["line"], 2)

        score = uploads.project_quality_score(session["upload_id"], "user-1")
        self.assertLess(score["score"], 100)
        self.assertEqual(score["files"][0]["path"], "nested/math.py")

    def test_project_upload_tracks_zero_divisor_variables(self):
        source = (
            "function divide(value) {\n"
            "  let divisor = 2;\n"
            "  divisor = 0;\n"
            "  return value / divisor;\n"
            "}\n"
        ).encode()
        session = self.create([{"path": "src/math.js", "size": len(source)}])
        self.put_file(session["upload_id"], 0, source)
        uploads.complete_upload(session["upload_id"], "user-1")

        with patch(
            "app.services.ai_reviewer.run_ai_review",
            return_value={"issues": [], "ai_available": False, "summary": ""},
        ):
            result = uploads.analyze_project_files(
                session["upload_id"],
                "user-1",
                [0],
                reset=True,
                selected_file_indices=[0],
            )

        findings = [
            issue for issue in result["results"][0]["static_issues"]
            if issue["source"] == "tree_sitter_value_tracking"
        ]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["line"], 4)

        score = uploads.project_quality_score(session["upload_id"], "user-1")
        self.assertLess(score["score"], 100)
        self.assertEqual(score["files"][0]["path"], "src/math.js")

    def test_identical_project_files_reuse_analysis_across_bounded_batches(self):
        source = b"function calculate(value) { return value + 1; }\n"
        session = self.create([
            {"path": "src/main.js", "size": len(source)},
            {"path": "copy/main.js", "size": len(source)},
        ])
        self.put_file(session["upload_id"], 0, source)
        self.put_file(session["upload_id"], 1, source)
        uploads.complete_upload(session["upload_id"], "user-1")

        with patch.dict(os.environ, {"PROJECT_MAX_ANALYSIS_REQUEST_FILES": "1"}):
            with patch(
                "app.services.code_analyzer.analyze_code",
                return_value={"issues": [], "supported": True},
            ) as analyze:
                with patch(
                    "app.services.ai_reviewer.run_ai_review",
                    return_value={"issues": [], "ai_available": True, "summary": ""},
                ) as ai_review:
                    uploads.analyze_project_files(
                        session["upload_id"],
                        "user-1",
                        [0],
                        reset=True,
                        selected_file_indices=[0, 1],
                    )
                    result = uploads.analyze_project_files(
                        session["upload_id"],
                        "user-1",
                        [1],
                    )

        self.assertEqual(analyze.call_count, 1)
        self.assertEqual(ai_review.call_count, 1)
        self.assertEqual(result["results"][0]["path"], "copy/main.js")
        self.assertTrue(result["results"][0]["analysis_reused"])
        self.assertEqual(result["results"][0]["duplicate_of"], "src/main.js")
        self.assertEqual(result["summary"]["reused"], 1)
        self.assertEqual(result["summary"]["duplicate_files"], 1)

    def test_analysis_only_processes_selected_files(self):
        session = self.create([
            {"path": "first.py", "size": 4},
            {"path": "second.py", "size": 4},
        ])
        self.put_chunk(session["upload_id"], 0, 0, b"a=1\n")
        self.put_chunk(session["upload_id"], 1, 0, b"b=2\n")
        uploads.complete_upload(session["upload_id"], "user-1")
        with patch("app.services.code_analyzer.analyze_code", return_value={"issues": []}) as analyze:
            with patch(
                "app.services.ai_reviewer.run_ai_review",
                return_value={"issues": [], "ai_available": True, "summary": ""},
            ) as ai_review:
                result = uploads.analyze_project_files(
                    session["upload_id"],
                    "user-1",
                    [1],
                    reset=True,
                    selected_file_indices=[1],
                )

        self.assertEqual(analyze.call_count, 1)
        self.assertEqual(ai_review.call_args.kwargs["code"], "b=2\n")
        self.assertEqual(result["results"][0]["path"], "second.py")
        self.assertEqual(result["summary"]["analyzed"], 1)
        self.assertTrue(result["summary"]["complete"])

    def test_quality_score_uses_persisted_findings_and_detects_stale_source(self):
        source = b"x = 1\n"
        session = self.create([{"path": "src/main.py", "size": len(source)}])
        self.put_file(session["upload_id"], 0, source)
        uploads.complete_upload(session["upload_id"], "user-1")
        finding = {
            "title": "Avoid unused variable",
            "line": 1,
            "severity": "warning",
            "category": "quality",
            "source": "pylint",
        }
        with patch(
            "app.services.code_analyzer.analyze_code",
            return_value={"issues": [finding], "supported": True},
        ):
            with patch(
                "app.services.ai_reviewer.run_ai_review",
                return_value={
                    "issues": [{**finding, "confidence": "high"}],
                    "ai_available": True,
                    "summary": "One finding.",
                },
            ):
                analysis = uploads.analyze_project_files(
                    session["upload_id"], "user-1", [0], reset=True, selected_file_indices=[0]
                )

        self.assertTrue(analysis["summary"]["complete"])
        score = uploads.project_quality_score(session["upload_id"], "user-1")
        self.assertEqual(score["score"], 95)
        self.assertEqual(score["coverage"]["analyzed"], 1)

        project_file = (
            Path(self.temporary_directory.name)
            / session["upload_id"]
            / "project"
            / "src"
            / "main.py"
        )
        project_file.write_bytes(b"x = 2\n")
        with self.assertRaisesRegex(uploads.ProjectUploadError, "changed"):
            uploads.project_quality_score(session["upload_id"], "user-1")

    def test_suggested_fix_requires_confirmation_and_can_be_reverted(self):
        source = b"value = 1\n"
        session = self.create([{"path": "src/main.py", "size": len(source)}])
        self.put_file(session["upload_id"], 0, source)
        uploads.complete_upload(session["upload_id"], "user-1")
        session_dir = Path(self.temporary_directory.name) / session["upload_id"]
        issue = {
            "title": "Replace the unsafe value",
            "line": 1,
            "severity": "warning",
            "category": "quality",
            "source": "ai",
            "confidence": "high",
        }
        uploads._write_state(session_dir, "analysis.json", {
            "selected_indices": [0],
            "processed_indices": [0],
            "complete": True,
            "results": {
                "0": {
                    "file_index": 0,
                    "path": "src/main.py",
                    "language": "python",
                    "issues": [issue],
                    "static_issues": [],
                    "ai_issues": [issue],
                    "source_sha256": hashlib.sha256(source).hexdigest(),
                },
            },
        })
        corrected = "value = 2\n"
        with patch(
            "app.services.auto_fix_service.auto_fix_code",
            return_value={
                "fixed_code": corrected,
                "summary": "Corrected the selected issue.",
                "ai_available": True,
            },
        ):
            with patch(
                "app.services.code_analyzer.analyze_code",
                return_value={"supported": True, "issues": []},
            ):
                proposal = uploads.create_fix_suggestion(
                    session["upload_id"], "user-1", 0, 0
                )

        project_file = session_dir / "project" / "src" / "main.py"
        self.assertEqual(project_file.read_bytes(), source)
        self.assertTrue(proposal["changed"])
        with self.assertRaisesRegex(uploads.ProjectUploadError, "explicit approval"):
            uploads.apply_fix_suggestion(
                session["upload_id"], "user-1", proposal["proposal_id"]
            )
        applied = uploads.apply_fix_suggestion(
            session["upload_id"],
            "user-1",
            proposal["proposal_id"],
            allow_validation_override=True,
        )
        self.assertEqual(project_file.read_text(encoding="utf-8"), corrected)
        self.assertTrue((session_dir / "history" / f"{applied['backup_id']}.bak").exists())

        restored = uploads.restore_project_backup(
            session["upload_id"], "user-1", applied["backup_id"]
        )
        self.assertEqual(project_file.read_bytes(), source)
        self.assertEqual(restored["path"], "src/main.py")

    def test_team_standard_fix_is_previewed_accepted_and_reanalyzed(self):
        source = 'api_key = "sk_live_FAKE_KEY_1234567890"\n'
        corrected = 'api_key = os.getenv("API_KEY")\n'
        issue = {
            "title": "[Team Standard] Never hardcode credentials",
            "standard_name": "Never hardcode credentials",
            "team_standard": True,
            "line": 1,
            "severity": "critical",
            "category": "security",
            "source": "team_standard",
            "recommendation": "Use an environment variable.",
        }
        session, _session_dir, project_file, source_bytes = self.create_analyzed_project_file(
            source, issue, "src/settings.py"
        )

        def analyze(language, code, **_kwargs):
            return {
                "supported": True,
                "issues": [issue] if code == source else [],
                "summary": {},
            }

        proposal_data = {
            "fixed_code": corrected,
            "original_region": source,
            "replacement_region": corrected,
            "patch_start": 0,
            "patch_end": len(source),
            "patch_start_line": 1,
            "patch_end_line": 1,
            "summary": "Read the key from the environment.",
            "ai_available": True,
        }
        with patch(
            "app.services.auto_fix_service.auto_fix_finding",
            return_value=proposal_data,
        ), patch(
            "app.services.code_analyzer.analyze_code",
            side_effect=analyze,
        ), patch(
            "app.services.firebase_service.firebase_available",
            return_value=True,
        ), patch(
            "app.services.firebase_service.get_team_standards_for_user",
            return_value=[{"title": "Never hardcode credentials", "enabled": True}],
        ), patch(
            "app.services.ai_reviewer.run_ai_review",
            return_value={"issues": [], "summary": "No remaining issues.", "ai_available": False},
        ):
            proposal = uploads.create_fix_suggestion(
                session["upload_id"], "user-1", 0, 0
            )
            self.assertTrue(proposal["changed"])
            self.assertEqual(proposal["original_code"], source)
            self.assertEqual(proposal["proposed_code"], corrected)
            self.assertEqual(project_file.read_bytes(), source_bytes)

            applied = uploads.apply_fix_suggestion(
                session["upload_id"], "user-1", proposal["proposal_id"]
            )
            self.assertEqual(project_file.read_text(encoding="utf-8"), corrected)

            analysis = uploads.analyze_project_files(
                session["upload_id"],
                "user-1",
                [0],
                reset=True,
                selected_file_indices=[0],
            )

        self.assertTrue(applied["backup_id"])
        self.assertEqual(analysis["results"][0]["issues"], [])

    def test_resolved_static_finding_passes_validation_for_automatic_apply(self):
        source = b"value = 1\n"
        issue = {
            "title": "Replace the unsafe value",
            "line": 1,
            "severity": "warning",
            "category": "quality",
            "source": "pylint",
        }
        session = self.create([{"path": "main.py", "size": len(source)}])
        self.put_file(session["upload_id"], 0, source)
        uploads.complete_upload(session["upload_id"], "user-1")
        session_dir = Path(self.temporary_directory.name) / session["upload_id"]
        uploads._write_state(session_dir, "analysis.json", {
            "selected_indices": [0],
            "processed_indices": [0],
            "complete": True,
            "results": {
                "0": {
                    "file_index": 0,
                    "path": "main.py",
                    "language": "python",
                    "issues": [issue],
                    "static_issues": [issue],
                    "ai_issues": [],
                    "source_sha256": hashlib.sha256(source).hexdigest(),
                },
            },
        })
        corrected = "value = 2\n"
        with patch(
            "app.services.auto_fix_service.auto_fix_code",
            return_value={
                "fixed_code": corrected,
                "summary": "Corrected the finding.",
                "ai_available": True,
            },
        ):
            with patch(
                "app.services.code_analyzer.analyze_code",
                side_effect=[
                    {"supported": True, "issues": [issue]},
                    {"supported": True, "issues": []},
                ],
            ):
                proposal = uploads.create_fix_suggestion(
                    session["upload_id"], "user-1", 0, 0
                )

        self.assertEqual(proposal["validation"]["passed"], True)
        applied = uploads.apply_fix_suggestion(
            session["upload_id"], "user-1", proposal["proposal_id"]
        )
        self.assertEqual(applied["updated_code"], corrected)

    def test_large_finding_proposal_applies_only_target_function_then_reanalyzes(self):
        source_code = (
            "def divide(value, divisor):\n"
            "    return value / divisor\n"
            + ("# unrelated project context\n" * 1_000)
            + "def multiply(left, right):\n"
            "    return left * right\n"
        )
        issue = {
            "title": "Possible division by zero",
            "description": "The divisor can be zero.",
            "line": 2,
            "severity": "warning",
            "category": "correctness",
            "source": "pylint",
        }
        session, session_dir, source_path, source = self.create_analyzed_project_file(
            source_code, issue
        )
        target_function = (
            "def divide(value, divisor):\n"
            "    if divisor == 0:\n"
            "        raise ValueError('divisor must not be zero')\n"
            "    return value / divisor\n"
        )
        original_function = "def divide(value, divisor):\n    return value / divisor\n"

        def fake_ai(**kwargs):
            self.assertEqual(kwargs["code"], original_function)
            self.assertTrue(kwargs["targeted_region"])
            self.assertEqual(kwargs["issues"][0]["title"], issue["title"])
            return {
                "fixed_code": target_function,
                "changes": [{"issue": issue["title"], "change": "Guard the divisor."}],
                "summary": "Added a zero-divisor guard.",
                "ai_available": True,
            }

        def validate(_language, candidate):
            findings = [issue] if "if divisor == 0" not in candidate else []
            return {"supported": True, "issues": findings, "has_syntax_error": False}

        with patch("app.services.auto_fix_service.auto_fix_code", side_effect=fake_ai):
            with patch("app.services.code_analyzer.analyze_code", side_effect=validate):
                proposal = uploads.create_fix_suggestion(
                    session["upload_id"], "user-1", 0, 0
                )

        self.assertGreater(len(source), 14_000)
        self.assertEqual(proposal["original_code"], original_function)
        self.assertEqual(proposal["proposed_code"], target_function)
        self.assertEqual(proposal["line_range"], {"start": 1, "end": 2})
        self.assertEqual(source_path.read_bytes(), source)

        applied = uploads.apply_fix_suggestion(
            session["upload_id"], "user-1", proposal["proposal_id"]
        )
        updated = source_path.read_text(encoding="utf-8")
        self.assertIn(target_function, updated)
        self.assertIn("# unrelated project context\n" * 1_000, updated)
        self.assertIn("def multiply(left, right):\n    return left * right\n", updated)
        self.assertEqual(applied["updated_code"], updated)

        with patch(
            "app.services.code_analyzer.analyze_code",
            return_value={"supported": True, "issues": [], "has_syntax_error": False},
        ):
            with patch(
                "app.services.ai_reviewer.run_ai_review",
                return_value={"issues": [], "ai_available": True, "summary": "No findings."},
            ):
                analysis = uploads.analyze_project_files(
                    session["upload_id"], "user-1", [0], reset=True, selected_file_indices=[0]
                )
        self.assertTrue(analysis["summary"]["complete"])
        self.assertEqual(analysis["results"][0]["issues"], [])
        self.assertEqual(
            uploads._read_state(session_dir, "analysis.json")["results"]["0"]["source_sha256"],
            hashlib.sha256(source_path.read_bytes()).hexdigest(),
        )

    def test_apply_rejects_patch_that_does_not_match_expected_region(self):
        source_code = "def check(value):\n    return value / 0\n"
        issue = {
            "title": "Possible division by zero",
            "line": 2,
            "severity": "warning",
            "source": "ai",
        }
        session, session_dir, source_path, source = self.create_analyzed_project_file(
            source_code, issue
        )
        replacement = "def check(value):\n    return value\n"
        with patch(
            "app.services.auto_fix_service.auto_fix_code",
            return_value={
                "fixed_code": replacement,
                "changes": [],
                "summary": "Updated the return.",
                "ai_available": True,
            },
        ):
            with patch(
                "app.services.code_analyzer.analyze_code",
                return_value={"supported": False, "issues": []},
            ):
                proposal = uploads.create_fix_suggestion(
                    session["upload_id"], "user-1", 0, 0
                )

        proposal_state = uploads._read_state(session_dir, "fix-suggestions.json")
        stored_proposal = proposal_state[proposal["proposal_id"]]
        stored_proposal["original_region"] = "not the source region"
        uploads._write_state(session_dir, "fix-suggestions.json", proposal_state)
        with self.assertRaisesRegex(uploads.ProjectUploadError, "maps safely"):
            uploads.apply_fix_suggestion(
                session["upload_id"],
                "user-1",
                proposal["proposal_id"],
                allow_validation_override=True,
            )
        self.assertEqual(source_path.read_bytes(), source)

    def test_syntax_failure_requires_explicit_approval_and_preserves_original(self):
        source_code = "def check(value):\n    return value / 0\n"
        issue = {
            "title": "Possible division by zero",
            "line": 2,
            "severity": "warning",
            "source": "pylint",
        }
        session, _session_dir, source_path, source = self.create_analyzed_project_file(
            source_code, issue
        )
        malformed_python = "def check(value):\n    if value >:\n        return value\n"

        def validate(_language, candidate):
            if candidate == malformed_python:
                return {
                    "supported": True,
                    "has_syntax_error": True,
                    "issues": [{"severity": "critical", "title": "Syntax error"}],
                }
            return {"supported": True, "has_syntax_error": False, "issues": [issue]}

        with patch(
            "app.services.auto_fix_service.auto_fix_code",
            return_value={
                "fixed_code": malformed_python,
                "changes": [],
                "summary": "Attempted a correction.",
                "ai_available": True,
            },
        ):
            with patch("app.services.code_analyzer.analyze_code", side_effect=validate):
                proposal = uploads.create_fix_suggestion(
                    session["upload_id"], "user-1", 0, 0
                )

        self.assertFalse(proposal["validation"]["passed"])
        self.assertIn("remains or static checks", proposal["validation"]["message"])
        self.assertEqual(source_path.read_bytes(), source)
        with self.assertRaisesRegex(uploads.ProjectUploadError, "explicit approval"):
            uploads.apply_fix_suggestion(
                session["upload_id"], "user-1", proposal["proposal_id"]
            )
        self.assertEqual(source_path.read_bytes(), source)

    def test_zip_project_upload_preserves_nested_paths(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("demo/src/main.py", "print('safe')\n")
            zip_file.writestr("demo/client/app.mjs", "const value = 1;\n")
            zip_file.writestr("demo/README.md", "Project")
            zip_file.writestr("demo/build/generated.js", "const ignored = 1;\n")

        project = uploads.create_upload_from_archive(
            "user-1", "demo.zip", archive.getvalue()
        )
        self.assertEqual(project["name"], "demo")
        self.assertEqual(
            [item["path"] for item in project["files"]],
            ["demo/src/main.py", "demo/client/app.mjs"],
        )
        self.assertEqual(
            [item["language"] for item in project["files"]],
            ["python", "javascript"],
        )
        self.assertEqual(
            {item["path"] for item in project["excluded_files"]},
            {"demo/README.md", "demo/build/generated.js"},
        )

    def test_mixed_language_zip_analysis_reports_skipped_entries(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr(
                "demo/src/app.mjs",
                "function divide(value) {\n  return value / 0;\n}\n",
            )
            zip_file.writestr(
                "demo/lib/math.py",
                "def divide(value):\n    return value / 0\n",
            )
            zip_file.writestr("demo/docs/README.md", "Documentation")
            zip_file.writestr("demo/node_modules/dep/index.js", "const ignored = 1;\n")
            zip_file.writestr("demo/vendor/library.js", "const ignored = 2;\n")

        with patch.dict(os.environ, {"PROJECT_UPLOAD_SKIP_DIRS": "vendor"}):
            project = uploads.create_upload_from_archive(
                "user-1", "demo.zip", archive.getvalue()
            )
        with patch(
            "app.services.ai_reviewer.run_ai_review",
            return_value={"issues": [], "ai_available": False, "summary": ""},
        ):
            analysis = uploads.analyze_project_files(
                project["upload_id"],
                "user-1",
                [0, 1],
                reset=True,
                selected_file_indices=[0, 1],
            )

        self.assertEqual(analysis["summary"]["languages"], ["javascript", "python"])
        self.assertEqual(analysis["summary"]["skipped"], 3)
        self.assertEqual(
            {item["path"] for item in analysis["summary"]["skipped_files"]},
            {
                "demo/docs/README.md",
                "demo/node_modules/dep/index.js",
                "demo/vendor/library.js",
            },
        )
        self.assertTrue(all(
            any(issue["title"] == "Division or remainder by zero literal" for issue in file["static_issues"])
            for file in analysis["results"]
        ))

    def test_zip_with_only_unsupported_files_reports_paths_and_reasons(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("demo/README.md", "Documentation")

        with self.assertRaises(uploads.ProjectUploadError) as unsupported:
            uploads.create_upload_from_archive("user-1", "demo.zip", archive.getvalue())

        self.assertIn("demo/README.md", unsupported.exception.detail)
        self.assertIn("Unsupported source file type", unsupported.exception.detail)

    def test_zip_source_file_limit_reports_unprocessed_paths(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("src/main.py", "print('safe')\n")
            zip_file.writestr("src/app.js", "console.log('safe');\n")
        skipped = []

        with patch("app.services.upload_service.MAX_FILES", 1):
            files = read_zip_archive(archive.getvalue(), skipped)

        self.assertEqual([item.path for item in files], ["src/main.py"])
        self.assertEqual(skipped, [{
            "path": "src/app.js",
            "reason": "ZIP source-file limit (1) was reached.",
        }])

    def test_authenticated_project_analysis_score_fix_and_restore_endpoints(self):
        from fastapi.testclient import TestClient

        from app.main import app

        source = b"x = 1\n"
        issue = {
            "title": "Use the corrected value",
            "line": 1,
            "severity": "warning",
            "category": "quality",
            "source": "ai",
            "confidence": "high",
        }

        with patch("app.routers.fix.firebase_auth.verify_id_token", return_value={"uid": "user-1"}):
            with patch(
                "app.services.code_analyzer.analyze_code",
                return_value={"issues": [], "supported": True},
            ):
                with patch(
                    "app.services.ai_reviewer.run_ai_review",
                    return_value={
                        "issues": [issue],
                        "ai_available": True,
                        "summary": "One finding.",
                    },
                ):
                    with patch(
                        "app.services.auto_fix_service.auto_fix_code",
                        return_value={
                            "fixed_code": "x = 2\n",
                            "summary": "Updated the selected issue.",
                            "ai_available": True,
                        },
                    ):
                        with TestClient(app) as client:
                            headers = {"Authorization": "Bearer valid"}
                            created = client.post(
                                "/api/fix/project-uploads",
                                headers=headers,
                                json={
                                    "name": "demo",
                                    "files": [{"path": "src/main.py", "size": len(source)}],
                                },
                            )
                            self.assertEqual(created.status_code, 201)
                            upload_id = created.json()["upload_id"]
                            for chunk_index, start in enumerate(range(0, len(source), 4)):
                                chunk = source[start:start + 4]
                                uploaded = client.put(
                                    f"/api/fix/project-uploads/{upload_id}/files/0/chunks/{chunk_index}",
                                    headers={
                                        **headers,
                                        "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
                                    },
                                    content=chunk,
                                )
                                self.assertEqual(uploaded.status_code, 200)
                            completed = client.post(
                                f"/api/fix/project-uploads/{upload_id}/complete",
                                headers=headers,
                            )
                            self.assertTrue(completed.json()["complete"])

                            analyzed = client.post(
                                f"/api/fix/project-uploads/{upload_id}/analyze",
                                headers=headers,
                                json={
                                    "file_indices": [0],
                                    "reset": True,
                                    "selected_file_indices": [0],
                                },
                            )
                            self.assertEqual(analyzed.status_code, 200)
                            self.assertEqual(analyzed.json()["results"][0]["issues"][0]["title"], issue["title"])
                            self.assertTrue(analyzed.json()["summary"]["complete"])

                            score = client.get(
                                f"/api/fix/project-uploads/{upload_id}/quality-score",
                                headers=headers,
                            )
                            self.assertEqual(score.status_code, 200)
                            self.assertEqual(score.json()["score"], 100)

                            proposal = client.post(
                                f"/api/fix/project-uploads/{upload_id}/fix-suggestions",
                                headers=headers,
                                json={"file_index": 0, "issue_index": 0},
                            )
                            self.assertEqual(proposal.status_code, 200)
                            self.assertEqual(proposal.json()["original_code"], source.decode())

                            applied = client.post(
                                f"/api/fix/project-uploads/{upload_id}/fix-suggestions/"
                                f"{proposal.json()['proposal_id']}/apply",
                                headers=headers,
                                json={"allow_validation_override": True},
                            )
                            self.assertEqual(applied.status_code, 200)
                            self.assertEqual(applied.json()["updated_code"], "x = 2\n")
                            self.assertTrue(applied.json()["backup_id"])

                            restored = client.post(
                                f"/api/fix/project-uploads/{upload_id}/restore/"
                                f"{applied.json()['backup_id']}",
                                headers=headers,
                            )
                            self.assertEqual(restored.status_code, 200)
                            self.assertIn("restored", restored.json()["message"].lower())

    def test_empty_and_invalid_zip_uploads_are_rejected(self):
        with self.assertRaises(uploads.ProjectUploadError):
            self.create([])
        with self.assertRaises(uploads.ProjectUploadError) as invalid:
            uploads.create_upload_from_archive("user-1", "broken.zip", b"not a zip")
        self.assertEqual(invalid.exception.status_code, 422)

    def test_streamed_archive_upload_preserves_paths(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("nested/src/app.py", "print('ok')\n")
        archive.seek(0)

        project = uploads.create_upload_from_archive_file(
            "user-1",
            "nested.zip",
            archive,
        )
        self.assertEqual(project["files"][0]["path"], "nested/src/app.py")
        self.assertEqual(project["storage_backend"], "local")
        stored_path = (
            Path(self.temporary_directory.name)
            / project["upload_id"]
            / "project"
            / "nested"
            / "src"
            / "app.py"
        )
        self.assertEqual(stored_path.read_text(encoding="utf-8"), "print('ok')\n")


    def test_project_upload_requires_bearer_authentication(self):
        with self.assertRaises(HTTPException) as unauthorized:
            asyncio.run(require_project_upload_user(None))
        self.assertEqual(unauthorized.exception.status_code, 401)

    def test_authenticated_http_chunk_upload_and_user_isolation(self):
        from fastapi.testclient import TestClient

        from app.main import app

        def verify_token(token):
            return {"uid": "user-2" if token == "other-user" else "user-1"}

        with patch.object(uploads, "chunk_bytes", return_value=4):
            with patch("app.routers.fix.firebase_auth.verify_id_token", side_effect=verify_token):
                with TestClient(app) as client:
                    response = client.post(
                        "/api/fix/project-uploads",
                        json={"name": "nested", "files": [{"path": "nested/main.py", "size": 8}]},
                    )
                    self.assertEqual(response.status_code, 401)

                    headers = {"Authorization": "Bearer valid-user"}
                    created = client.post(
                        "/api/fix/project-uploads",
                        headers=headers,
                        json={"name": "nested", "files": [{"path": "nested/main.py", "size": 8}]},
                    )
                    self.assertEqual(created.status_code, 201)
                    upload_id = created.json()["upload_id"]

                    chunk = b"abcd"
                    chunk_headers = {
                        **headers,
                        "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
                    }
                    first = client.put(
                        f"/api/fix/project-uploads/{upload_id}/files/0/chunks/0",
                        headers=chunk_headers,
                        content=chunk,
                    )
                    self.assertEqual(first.status_code, 200)
                    duplicate = client.put(
                        f"/api/fix/project-uploads/{upload_id}/files/0/chunks/0",
                        headers=chunk_headers,
                        content=chunk,
                    )
                    self.assertTrue(duplicate.json()["duplicate"])

                    other_user = client.get(
                        f"/api/fix/project-uploads/{upload_id}",
                        headers={"Authorization": "Bearer other-user"},
                    )
                    self.assertEqual(other_user.status_code, 404)

                    missing_chunk = client.post(
                        f"/api/fix/project-uploads/{upload_id}/complete",
                        headers=headers,
                    )
                    self.assertEqual(missing_chunk.status_code, 409)

                    final_chunk = b"efgh"
                    final_response = client.put(
                        f"/api/fix/project-uploads/{upload_id}/files/0/chunks/1",
                        headers={
                            **headers,
                            "X-Chunk-SHA256": hashlib.sha256(final_chunk).hexdigest(),
                        },
                        content=final_chunk,
                    )
                    self.assertTrue(final_response.json()["file_complete"])
                    completed = client.post(
                        f"/api/fix/project-uploads/{upload_id}/complete",
                        headers=headers,
                    )
                    self.assertTrue(completed.json()["complete"])

    def test_authenticated_http_archive_upload_preserves_paths(self):
        from fastapi.testclient import TestClient

        from app.main import app

        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("nested/src/app.py", "print('ok')\n")
        with patch("app.auth.firebase_auth.verify_id_token", return_value={"uid": "user-1"}):
            with TestClient(app) as client:
                response = client.post(
                    "/api/fix/project-uploads/archive",
                    headers={"Authorization": "Bearer valid-token"},
                    files={"file": ("nested.zip", archive.getvalue(), "application/zip")},
                )
        self.assertEqual(response.status_code, 201)
        project = response.json()
        self.assertEqual(project["files"][0]["path"], "nested/src/app.py")
        self.assertEqual(project["storage_backend"], "local")

    def test_project_metadata_routes_reject_uid_spoofing(self):
        from fastapi.testclient import TestClient

        from app.main import app

        with patch("app.auth.firebase_auth.verify_id_token", return_value={"uid": "user-1"}):
            with TestClient(app) as client:
                headers = {"Authorization": "Bearer valid-token"}
                created = client.post(
                    "/api/projects",
                    headers=headers,
                    json={"user_id": "user-2", "name": "other user's project"},
                )
                self.assertEqual(created.status_code, 403)

                listed = client.get(
                    "/api/projects?user_id=user-2",
                    headers=headers,
                )
                self.assertEqual(listed.status_code, 403)

    def test_review_endpoints_require_and_enforce_authenticated_uid(self):
        from fastapi.testclient import TestClient

        from app.main import app

        with patch("app.auth.firebase_auth.verify_id_token", return_value={"uid": "user-1"}):
            with TestClient(app) as client:
                body = {
                    "user_id": "user-2",
                    "project_name": "demo",
                    "language": "Python",
                    "code": "print('not submitted')\n",
                }
                spoofed_review = client.post(
                    "/api/review",
                    headers={"Authorization": "Bearer valid-token"},
                    json=body,
                )
                self.assertEqual(spoofed_review.status_code, 403)

                unauthenticated_history = client.get("/api/reviews?user_id=user-1")
                self.assertEqual(unauthenticated_history.status_code, 401)


if __name__ == "__main__":
    unittest.main()
