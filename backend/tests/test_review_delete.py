import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from google.cloud.firestore_v1 import LastUpdateOption

from app.main import app
from app.routers import reviews as reviews_router
from app.services import firebase_service


class _Snapshot:
    def __init__(self, document_id, data, collection=None):
        self.id = document_id
        self.data = data
        self.exists = data is not None
        self.update_time = (
            collection.update_times.setdefault(
                document_id,
                datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
            if self.exists and collection is not None
            else None
        )

    def to_dict(self):
        return dict(self.data or {})

    def get(self, key, default=None):
        return (self.data or {}).get(key, default)


class _DocumentReference:
    def __init__(self, collection, document_id):
        self.collection = collection
        self.id = document_id

    def get(self, transaction=None):
        if transaction is not None:
            if not transaction.in_progress:
                raise ValueError("Transaction not in progress, cannot be used in API requests.")
            if transaction.writes_started:
                raise AssertionError("Transactional reads must happen before writes.")
            transaction.read_count += 1
            transaction.reads_before_first_write = transaction.read_count
        return _Snapshot(self.id, self.collection.documents.get(self.id), self.collection)

    def set(self, data):
        self.collection.documents[self.id] = dict(data)
        self.collection.update_times[self.id] = datetime.now(timezone.utc)

    def delete(self):
        self.collection.documents.pop(self.id, None)
        self.collection.update_times.pop(self.id, None)


class _Query:
    def __init__(self, collection, field, value):
        self.collection = collection
        self.field = field
        self.value = value

    def stream(self):
        return [
            _Snapshot(document_id, data, self.collection)
            for document_id, data in self.collection.documents.items()
            if data.get(self.field) == self.value
        ]


class _Collection:
    def __init__(self, name):
        self.name = name
        self.documents = {}
        self.update_times = {}

    def document(self, document_id):
        return _DocumentReference(self, document_id)

    def where(self, field, operator, value):
        if operator != "==":
            raise AssertionError(f"Unexpected query operator: {operator}")
        return _Query(self, field, value)


class _Transaction:
    def __init__(self, database):
        self.database = database
        self.operations = []
        self._id = None
        self._read_only = False
        self._max_attempts = 5
        self.begin_count = 0
        self.commit_attempt_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.read_count = 0
        self.write_count = 0
        self.reads_before_first_write = 0
        self.writes_started = False

    @property
    def in_progress(self):
        return self._id is not None

    def _clean_up(self):
        self._id = None
        self.operations = []
        self.writes_started = False

    def _begin(self, retry_id=None):
        if self.in_progress:
            raise ValueError("Transaction has already started.")
        self.begin_count += 1
        self._id = retry_id or f"fake-transaction-{self.begin_count}".encode("ascii")

    def set(self, reference, data):
        if not self.in_progress:
            raise ValueError("Transaction is not in progress.")
        self.writes_started = True
        self.write_count += 1
        self.operations.append(("set", reference, dict(data)))

    def delete(self, reference):
        if not self.in_progress:
            raise ValueError("Transaction is not in progress.")
        self.writes_started = True
        self.write_count += 1
        self.operations.append(("delete", reference, None))

    def _commit(self):
        if not self.in_progress:
            raise ValueError("Transaction is not in progress.")
        self.commit_attempt_count += 1
        if self.database.fail_next_commit:
            self.database.fail_next_commit = False
            raise RuntimeError("Simulated transaction commit failure.")

        updates = {}
        for operation, reference, data in self.operations:
            documents = updates.setdefault(
                reference.collection,
                dict(reference.collection.documents),
            )
            if operation == "set":
                documents[reference.id] = dict(data)
            else:
                documents.pop(reference.id, None)
        for collection, documents in updates.items():
            collection.documents.clear()
            collection.documents.update(documents)
        self.commit_count += 1
        self._clean_up()

    def _rollback(self):
        self.rollback_count += 1
        self._clean_up()


class _Database:
    def __init__(self):
        self.collections = {}
        self.transactions = []
        self.fail_next_commit = False
        self.batches = []
        self.fail_next_batch_commit = False
        self.before_batch_commit = None

    def collection(self, name):
        return self.collections.setdefault(name, _Collection(name))

    def transaction(self):
        transaction = _Transaction(self)
        self.transactions.append(transaction)
        return transaction

    def batch(self):
        batch = _Batch(self)
        self.batches.append(batch)
        return batch


class _Batch:
    def __init__(self, database):
        self.database = database
        self.deletes = []

    def delete(self, reference, option=None):
        self.deletes.append((reference, option))

    def commit(self):
        if self.database.fail_next_batch_commit:
            self.database.fail_next_batch_commit = False
            raise RuntimeError("Simulated batch commit failure.")
        if self.database.before_batch_commit is not None:
            callback = self.database.before_batch_commit
            self.database.before_batch_commit = None
            callback()

        for reference, option in self.deletes:
            if not isinstance(option, LastUpdateOption):
                raise AssertionError("Cleanup deletes must use an update-time precondition.")
            if (
                reference.id not in reference.collection.documents
                or option._last_update_time
                != reference.collection.update_times.get(reference.id)
            ):
                raise RuntimeError("Batch delete precondition failed.")

        for reference, _ in self.deletes:
            reference.delete()


class ReviewTrashTests(unittest.TestCase):
    RELATED_COLLECTIONS = (
        firebase_service.REVIEW_ISSUES,
        firebase_service.FEEDBACK,
        "hindsight_experiences",
        "hindsight_feedback",
        firebase_service.PROJECTS,
    )

    def setUp(self):
        self.database = _Database()
        self.database.collection(firebase_service.CODE_REVIEWS).documents["review-1"] = {
            "author_id": "user-1",
            "project_name": "payments",
            "analysis": {"summary": "Complete review result"},
        }
        for collection_name in self.RELATED_COLLECTIONS:
            self.database.collection(collection_name).documents["linked-1"] = {
                "review_id": "review-1",
                "untouched": True,
            }
        self.secret = patch.dict(os.environ, {"REVIEW_CURSOR_SECRET": "test-review-cursor-secret"})
        self.secret.start()
        self.addCleanup(self.secret.stop)

    def _request(self, method, path, user_id=None):
        headers = {}
        if user_id is not None:
            headers["Authorization"] = "Bearer test-token"

        with (
            patch.object(reviews_router, "firebase_available", return_value=True),
            patch.object(firebase_service, "get_db", return_value=self.database),
            patch(
                "app.auth.firebase_auth.verify_id_token",
                return_value={"uid": user_id},
            ),
        ):
            with TestClient(app) as client:
                return client.request(method, path, headers=headers)

    def _assert_related_documents_untouched(self):
        for collection_name in self.RELATED_COLLECTIONS:
            self.assertEqual(
                self.database.collection(collection_name).documents["linked-1"],
                {"review_id": "review-1", "untouched": True},
            )

    def _assert_transaction_lifecycle(
        self,
        *,
        expected_reads,
        expected_writes,
        committed=True,
        commit_attempts=1,
    ):
        transaction = self.database.transactions[-1]
        self.assertEqual(transaction.begin_count, 1)
        self.assertEqual(transaction.read_count, expected_reads)
        self.assertEqual(transaction.write_count, expected_writes)
        self.assertEqual(transaction.reads_before_first_write, expected_reads)
        self.assertEqual(transaction.commit_attempt_count, commit_attempts)
        self.assertEqual(transaction.commit_count, 1 if committed else 0)
        self.assertEqual(transaction.rollback_count, 0 if committed else 1)

    def _seed_trash_review(self, review_id="review-1", owner="user-1", expires_at=None, **extra):
        deleted_at = datetime.now(timezone.utc) - timedelta(days=1)
        data = {
            "author_id": owner,
            "project_name": "payments",
            "deleted_at": deleted_at,
            "expires_at": expires_at or deleted_at + timedelta(days=30),
            **extra,
        }
        self.database.collection(firebase_service.DELETED_CODE_REVIEWS).documents[review_id] = data
        return data

    def test_owner_delete_moves_review_to_trash_and_preserves_id_and_data(self):
        response = self._request("DELETE", "/api/reviews/review-1", user_id="user-1")

        self.assertEqual(response.status_code, 204)
        self.assertNotIn(
            "review-1",
            self.database.collection(firebase_service.CODE_REVIEWS).documents,
        )
        trash_data = self.database.collection(
            firebase_service.DELETED_CODE_REVIEWS
        ).documents["review-1"]
        self.assertEqual(trash_data["author_id"], "user-1")
        self.assertEqual(trash_data["project_name"], "payments")
        self.assertEqual(trash_data["analysis"], {"summary": "Complete review result"})
        self.assertIsInstance(trash_data["deleted_at"], datetime)
        self.assertEqual(
            trash_data["expires_at"] - trash_data["deleted_at"],
            timedelta(days=30),
        )
        self._assert_transaction_lifecycle(expected_reads=2, expected_writes=2)
        self._assert_related_documents_untouched()

    def test_unauthenticated_delete_is_rejected(self):
        response = self._request("DELETE", "/api/reviews/review-1")

        self.assertEqual(response.status_code, 401)
        self.assertIn("review-1", self.database.collection(firebase_service.CODE_REVIEWS).documents)

    def test_non_owner_delete_is_hidden_as_not_found(self):
        response = self._request("DELETE", "/api/reviews/review-1", user_id="user-2")

        self.assertEqual(response.status_code, 404)
        self.assertIn("review-1", self.database.collection(firebase_service.CODE_REVIEWS).documents)
        self.assertNotIn(
            "review-1",
            self.database.collection(firebase_service.DELETED_CODE_REVIEWS).documents,
        )

    def test_missing_review_returns_not_found(self):
        response = self._request("DELETE", "/api/reviews/missing-review", user_id="user-1")

        self.assertEqual(response.status_code, 404)

    def test_existing_trash_document_is_not_overwritten(self):
        existing_trash = {"author_id": "user-1", "marker": "preserve"}
        self.database.collection(firebase_service.DELETED_CODE_REVIEWS).documents[
            "review-1"
        ] = existing_trash

        response = self._request("DELETE", "/api/reviews/review-1", user_id="user-1")

        self.assertEqual(response.status_code, 409)
        self.assertIn("review-1", self.database.collection(firebase_service.CODE_REVIEWS).documents)
        self.assertEqual(
            self.database.collection(firebase_service.DELETED_CODE_REVIEWS).documents["review-1"],
            existing_trash,
        )

    def test_legacy_author_id_is_supported_when_moving_to_trash(self):
        reviews = self.database.collection(firebase_service.CODE_REVIEWS).documents
        reviews["legacy-review"] = {"authorId": "user-1", "legacy_field": "retained"}

        response = self._request("DELETE", "/api/reviews/legacy-review", user_id="user-1")

        self.assertEqual(response.status_code, 204)
        trashed = self.database.collection(firebase_service.DELETED_CODE_REVIEWS).documents[
            "legacy-review"
        ]
        self.assertEqual(trashed["authorId"], "user-1")
        self.assertEqual(trashed["author_id"], "user-1")
        self.assertEqual(trashed["legacy_field"], "retained")

    def test_owner_can_restore_review_and_related_records_remain_untouched(self):
        self.database.collection(firebase_service.CODE_REVIEWS).documents.pop("review-1")
        self._seed_trash_review(extra_field="retained")

        response = self._request(
            "POST",
            "/api/reviews/trash/review-1/restore",
            user_id="user-1",
        )

        self.assertEqual(response.status_code, 204)
        restored = self.database.collection(firebase_service.CODE_REVIEWS).documents["review-1"]
        self.assertEqual(restored["author_id"], "user-1")
        self.assertEqual(restored["extra_field"], "retained")
        self.assertNotIn("deleted_at", restored)
        self.assertNotIn("expires_at", restored)
        self.assertNotIn(
            "review-1",
            self.database.collection(firebase_service.DELETED_CODE_REVIEWS).documents,
        )
        self._assert_transaction_lifecycle(expected_reads=2, expected_writes=2)
        self._assert_related_documents_untouched()

    def test_non_owner_cannot_restore_trash_review(self):
        self._seed_trash_review()

        response = self._request(
            "POST",
            "/api/reviews/trash/review-1/restore",
            user_id="user-2",
        )

        self.assertEqual(response.status_code, 404)
        self.assertIn(
            "review-1",
            self.database.collection(firebase_service.DELETED_CODE_REVIEWS).documents,
        )

    def test_expired_review_cannot_be_restored(self):
        self.database.collection(firebase_service.CODE_REVIEWS).documents.pop("review-1")
        self._seed_trash_review(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))

        response = self._request(
            "POST",
            "/api/reviews/trash/review-1/restore",
            user_id="user-1",
        )

        self.assertEqual(response.status_code, 410)
        self.assertNotIn("review-1", self.database.collection(firebase_service.CODE_REVIEWS).documents)
        self.assertIn(
            "review-1",
            self.database.collection(firebase_service.DELETED_CODE_REVIEWS).documents,
        )

    def test_restore_does_not_overwrite_existing_active_review(self):
        self._seed_trash_review()
        active = self.database.collection(firebase_service.CODE_REVIEWS).documents["review-1"]

        response = self._request(
            "POST",
            "/api/reviews/trash/review-1/restore",
            user_id="user-1",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            self.database.collection(firebase_service.CODE_REVIEWS).documents["review-1"],
            active,
        )
        self.assertIn(
            "review-1",
            self.database.collection(firebase_service.DELETED_CODE_REVIEWS).documents,
        )

    def test_owner_can_permanently_delete_only_trash_document(self):
        self._seed_trash_review()

        response = self._request(
            "DELETE",
            "/api/reviews/trash/review-1",
            user_id="user-1",
        )

        self.assertEqual(response.status_code, 204)
        self.assertNotIn(
            "review-1",
            self.database.collection(firebase_service.DELETED_CODE_REVIEWS).documents,
        )
        self._assert_transaction_lifecycle(expected_reads=1, expected_writes=1)
        self.assertIn("review-1", self.database.collection(firebase_service.CODE_REVIEWS).documents)
        self._assert_related_documents_untouched()

    def test_move_commit_failure_leaves_the_active_review_intact(self):
        self.database.fail_next_commit = True

        response = self._request("DELETE", "/api/reviews/review-1", user_id="user-1")

        self.assertEqual(response.status_code, 500)
        self.assertIn("review-1", self.database.collection(firebase_service.CODE_REVIEWS).documents)
        self.assertNotIn(
            "review-1",
            self.database.collection(firebase_service.DELETED_CODE_REVIEWS).documents,
        )
        self._assert_transaction_lifecycle(
            expected_reads=2,
            expected_writes=2,
            committed=False,
            commit_attempts=1,
        )

    def test_non_owner_cannot_permanently_delete_trash_document(self):
        self._seed_trash_review()

        response = self._request(
            "DELETE",
            "/api/reviews/trash/review-1",
            user_id="user-2",
        )

        self.assertEqual(response.status_code, 404)
        self.assertIn(
            "review-1",
            self.database.collection(firebase_service.DELETED_CODE_REVIEWS).documents,
        )

    def test_trash_pagination_filters_owner_expiration_and_uses_signed_cursor(self):
        now = datetime.now(timezone.utc)
        documents = self.database.collection(firebase_service.DELETED_CODE_REVIEWS).documents
        for index in range(12):
            documents[f"user-1-{index:02}"] = {
                "author_id": "user-1",
                "expires_at": now + timedelta(days=10, seconds=index),
            }
        documents["other-user"] = {
            "author_id": "user-2",
            "expires_at": now + timedelta(days=10),
        }
        documents["expired"] = {
            "author_id": "user-1",
            "expires_at": now - timedelta(seconds=1),
        }

        first = self._request(
            "GET",
            "/api/reviews/trash/page?user_id=user-1",
            user_id="user-1",
        )
        self.assertEqual(first.status_code, 200)
        first_page = first.json()
        self.assertEqual(first_page["total_count"], 12)
        self.assertEqual(len(first_page["reviews"]), 10)
        self.assertIsNotNone(first_page["next_cursor"])
        self.assertTrue(all(item["author_id"] == "user-1" for item in first_page["reviews"]))

        second = self._request(
            "GET",
            f"/api/reviews/trash/page?user_id=user-1&cursor={first_page['next_cursor']}",
            user_id="user-1",
        )
        self.assertEqual(second.status_code, 200)
        second_page = second.json()
        self.assertEqual(second_page["total_count"], 12)
        self.assertEqual(len(second_page["reviews"]), 2)
        first_ids = {item["id"] for item in first_page["reviews"]}
        second_ids = {item["id"] for item in second_page["reviews"]}
        self.assertFalse(first_ids & second_ids)
        self.assertFalse(first_ids & {"expired", "other-user"})
        self.assertNotIn("expired", documents)

    def test_trash_cleanup_deletes_only_owned_expired_documents_at_the_boundary(self):
        now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
        documents = self.database.collection(firebase_service.DELETED_CODE_REVIEWS).documents
        documents.update({
            "expired-owner": {
                "author_id": "user-1",
                "expires_at": now - timedelta(microseconds=1),
            },
            "boundary-legacy-owner": {
                "authorId": "user-1",
                "expires_at": now,
            },
            "future-owner": {
                "author_id": "user-1",
                "expires_at": now + timedelta(microseconds=1),
            },
            "expired-other-owner": {
                "author_id": "user-2",
                "expires_at": now - timedelta(days=1),
            },
        })
        active_reviews = self.database.collection(firebase_service.CODE_REVIEWS).documents
        active_reviews["active-expired"] = {
            "author_id": "user-1",
            "expires_at": now - timedelta(days=1),
        }

        with patch.object(firebase_service, "_utc_now", return_value=now):
            response = self._request(
                "GET",
                "/api/reviews/trash/page?user_id=user-1",
                user_id="user-1",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_count"], 1)
        self.assertEqual([item["id"] for item in response.json()["reviews"]], ["future-owner"])
        self.assertNotIn("expired-owner", documents)
        self.assertNotIn("boundary-legacy-owner", documents)
        self.assertIn("future-owner", documents)
        self.assertIn("expired-other-owner", documents)
        self.assertIn("active-expired", active_reviews)
        self.assertEqual(len(self.database.batches), 1)
        self.assertTrue(all(
            reference.collection.name == firebase_service.DELETED_CODE_REVIEWS
            and isinstance(option, LastUpdateOption)
            for reference, option in self.database.batches[0].deletes
        ))
        self._assert_related_documents_untouched()

    def test_trash_cleanup_batch_failure_is_reported_without_deleting_documents(self):
        now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
        documents = self.database.collection(firebase_service.DELETED_CODE_REVIEWS).documents
        documents["expired"] = {
            "author_id": "user-1",
            "expires_at": now - timedelta(seconds=1),
        }
        documents["not-expired"] = {
            "author_id": "user-1",
            "expires_at": now + timedelta(seconds=1),
        }
        self.database.fail_next_batch_commit = True

        with patch.object(firebase_service, "_utc_now", return_value=now):
            response = self._request(
                "GET",
                "/api/reviews/trash/page?user_id=user-1",
                user_id="user-1",
            )

        self.assertEqual(response.status_code, 500)
        self.assertIn("expired", documents)
        self.assertIn("not-expired", documents)
        self.assertIn("review-1", self.database.collection(firebase_service.CODE_REVIEWS).documents)
        self._assert_related_documents_untouched()

    def test_concurrent_expiry_extension_fails_the_update_time_precondition(self):
        now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
        documents = self.database.collection(firebase_service.DELETED_CODE_REVIEWS).documents
        documents["review-being-extended"] = {
            "author_id": "user-1",
            "expires_at": now - timedelta(seconds=1),
        }

        def extend_expiry():
            documents["review-being-extended"]["expires_at"] = now + timedelta(days=1)
            self.database.collection(
                firebase_service.DELETED_CODE_REVIEWS
            ).update_times["review-being-extended"] = now + timedelta(seconds=1)

        self.database.before_batch_commit = extend_expiry
        with patch.object(firebase_service, "_utc_now", return_value=now):
            response = self._request(
                "GET",
                "/api/reviews/trash/page?user_id=user-1",
                user_id="user-1",
            )

        self.assertEqual(response.status_code, 500)
        self.assertGreater(
            documents["review-being-extended"]["expires_at"],
            now,
        )
        self.assertIn("review-being-extended", documents)

    def test_trash_cleanup_splits_deletes_into_batches_of_at_most_500(self):
        now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
        documents = self.database.collection(firebase_service.DELETED_CODE_REVIEWS).documents
        for index in range(501):
            documents[f"expired-{index}"] = {
                "author_id": "user-1",
                "expires_at": now - timedelta(seconds=1),
            }

        with patch.object(firebase_service, "_utc_now", return_value=now):
            response = self._request(
                "GET",
                "/api/reviews/trash/page?user_id=user-1",
                user_id="user-1",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_count"], 0)
        self.assertEqual(len(self.database.batches), 2)
        self.assertEqual(
            [len(batch.deletes) for batch in self.database.batches],
            [500, 1],
        )
        self.assertEqual(documents, {})

    def test_tampered_trash_cursor_is_rejected(self):
        documents = self.database.collection(firebase_service.DELETED_CODE_REVIEWS).documents
        expires_at = datetime.now(timezone.utc) + timedelta(days=1)
        for index in range(11):
            documents[f"review-{index}"] = {
                "author_id": "user-1",
                "expires_at": expires_at + timedelta(seconds=index),
            }
        first = self._request(
            "GET",
            "/api/reviews/trash/page?user_id=user-1",
            user_id="user-1",
        ).json()
        cursor = first["next_cursor"]
        payload, signature = cursor.split(".", 1)
        tampered_cursor = f"{payload[:-1]}{'A' if payload[-1] != 'A' else 'B'}.{signature}"

        response = self._request(
            "GET",
            f"/api/reviews/trash/page?user_id=user-1&cursor={tampered_cursor}",
            user_id="user-1",
        )

        self.assertEqual(response.status_code, 400)

    def test_trash_pagination_supports_legacy_author_id(self):
        self.database.collection(firebase_service.DELETED_CODE_REVIEWS).documents[
            "legacy-review"
        ] = {
            "authorId": "user-1",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
        }

        response = self._request(
            "GET",
            "/api/reviews/trash/page?user_id=user-1",
            user_id="user-1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_count"], 1)
        self.assertEqual(response.json()["reviews"][0]["id"], "legacy-review")

    def test_unauthenticated_trash_request_is_rejected(self):
        requests = (
            ("GET", "/api/reviews/trash/page?user_id=user-1"),
            ("POST", "/api/reviews/trash/review-1/restore"),
            ("DELETE", "/api/reviews/trash/review-1"),
        )
        for method, path in requests:
            with self.subTest(method=method, path=path):
                response = self._request(method, path)
                self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
