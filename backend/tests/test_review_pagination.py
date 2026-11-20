import base64
import hashlib
import hmac
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.routers import reviews as reviews_router
from app.services import firebase_service


class _DocumentSnapshot:
    def __init__(self, document_id, data):
        self.id = document_id
        self.data = data

    def get(self, field):
        return self.data.get(field)

    def to_dict(self):
        return dict(self.data)


class _DocumentReference:
    def __init__(self, document_id):
        self.id = document_id


class _CountQuery:
    def __init__(self, query):
        self.query = query

    def get(self):
        return [[SimpleNamespace(value=len(self.query.matching_documents()))]]


class _Query:
    def __init__(self, collection):
        self.collection = collection
        self.filters = []
        self.orderings = []
        self.cursor = None
        self.max_results = None

    def where(self, field, operator, expected):
        self.filters.append((field, operator, expected))
        return self

    def order_by(self, field, direction="ASCENDING"):
        self.orderings.append((field, direction))
        return self

    def limit(self, count):
        self.max_results = count
        return self

    def start_after(self, values):
        self.cursor = values
        return self

    def count(self, alias=None):
        return _CountQuery(self)

    def matching_documents(self):
        documents = [
            _DocumentSnapshot(document_id, data)
            for document_id, data in self.collection.documents.items()
            if all(data.get(field) == expected for field, operator, expected in self.filters)
        ]
        for field, direction in reversed(self.orderings):
            documents.sort(
                key=lambda document: document.id if field == "__name__" else document.get(field),
                reverse=direction == "DESCENDING",
            )
        if self.cursor is not None:
            cursor_created_at, cursor_document = self.cursor
            cursor_key = (cursor_created_at, cursor_document.id)
            documents = [
                document for document in documents
                if (document.get("created_at"), document.id) < cursor_key
            ]
        return documents

    def stream(self):
        documents = self.matching_documents()
        return documents[:self.max_results] if self.max_results is not None else documents


class _Collection:
    def __init__(self):
        self.documents = {}

    def where(self, field, operator, expected):
        return _Query(self).where(field, operator, expected)

    def document(self, document_id):
        return _DocumentReference(document_id)


class _Database:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, _Collection())


class ReviewPaginationTests(unittest.TestCase):
    def setUp(self):
        self.database = _Database()
        self.base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.cursor_secret = "unit-test-review-cursor-secret-stable-value"
        environment = patch.dict(
            os.environ,
            {"REVIEW_CURSOR_SECRET": self.cursor_secret},
        )
        environment.start()
        self.addCleanup(environment.stop)

    def _add_reviews(self, user_id, count, start=0):
        documents = self.database.collection(firebase_service.CODE_REVIEWS).documents
        for index in range(start, start + count):
            document_id = f"{user_id}-review-{index:02d}"
            documents[document_id] = {
                "author_id": user_id,
                "project_id": "project-1",
                "project_name": f"Project {index}",
                "language": "python",
                "code": f"value = {index}",
                "status": "completed",
                "created_at": self.base_time + timedelta(minutes=index),
                "summary": f"Summary {index}",
            }

    def _get_page(self, user_id="user-1", cursor=None):
        with patch.object(firebase_service, "get_db", return_value=self.database):
            return firebase_service.get_code_reviews_page(user_id, cursor)

    def _cursor_payload(self, cursor):
        encoded_payload = cursor.split(".", 1)[0]
        raw_payload = base64.urlsafe_b64decode(
            encoded_payload + "=" * (-len(encoded_payload) % 4)
        )
        return json.loads(raw_payload.decode("utf-8"))

    def _replace_cursor_payload(self, cursor, payload):
        _, signature = cursor.split(".", 1)
        encoded_payload = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).decode("ascii").rstrip("=")
        return f"{encoded_payload}.{signature}"

    def _sign_cursor_payload(self, payload):
        encoded_payload = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).decode("ascii").rstrip("=")
        signature = base64.urlsafe_b64encode(
            hmac.new(
                self.cursor_secret.encode("utf-8"),
                encoded_payload.encode("ascii"),
                hashlib.sha256,
            ).digest()
        ).decode("ascii").rstrip("=")
        return f"{encoded_payload}.{signature}"

    def test_first_page_returns_at_most_ten_newest_reviews(self):
        self._add_reviews("user-1", 13)

        page = self._get_page()

        self.assertEqual(len(page["reviews"]), 10)
        self.assertEqual(page["total_count"], 13)
        self.assertIsNotNone(page["next_cursor"])
        self.assertEqual(
            [review["id"] for review in page["reviews"]],
            [f"user-1-review-{index:02d}" for index in range(12, 2, -1)],
        )
        self.assertIsInstance(page["reviews"][0]["created_at"], str)
        self.assertEqual(page["reviews"][0]["summary"], "Summary 12")

    def test_second_page_has_no_duplicates_and_returns_remaining_reviews(self):
        self._add_reviews("user-1", 20)
        first_page = self._get_page()

        second_page = self._get_page(cursor=first_page["next_cursor"])

        first_ids = {review["id"] for review in first_page["reviews"]}
        second_ids = {review["id"] for review in second_page["reviews"]}
        self.assertEqual(len(second_page["reviews"]), 10)
        self.assertFalse(first_ids.intersection(second_ids))
        self.assertEqual(second_page["total_count"], 20)
        self.assertIsNone(second_page["next_cursor"])

    def test_valid_signed_cursor_continues_to_next_page(self):
        self._add_reviews("user-1", 12)
        first_page = self._get_page()
        cursor = first_page["next_cursor"]
        encoded_payload, signature = cursor.split(".", 1)
        expected_signature = base64.urlsafe_b64encode(
            hmac.new(
                self.cursor_secret.encode("utf-8"),
                encoded_payload.encode("ascii"),
                hashlib.sha256,
            ).digest()
        ).decode("ascii").rstrip("=")

        second_page = self._get_page(cursor=cursor)

        self.assertTrue(hmac.compare_digest(signature, expected_signature))
        self.assertEqual(
            [review["id"] for review in second_page["reviews"]],
            ["user-1-review-01", "user-1-review-00"],
        )

    def test_equal_timestamps_use_document_id_for_stable_pagination(self):
        self._add_reviews("user-1", 12)
        documents = self.database.collection(firebase_service.CODE_REVIEWS).documents
        for document in documents.values():
            document["created_at"] = self.base_time

        first_page = self._get_page()
        second_page = self._get_page(cursor=first_page["next_cursor"])

        combined_ids = [review["id"] for review in first_page["reviews"] + second_page["reviews"]]
        self.assertEqual(
            combined_ids,
            [f"user-1-review-{index:02d}" for index in range(11, -1, -1)],
        )

    def test_owner_filter_applies_to_page_and_count(self):
        self._add_reviews("user-1", 12)
        self._add_reviews("user-2", 7)

        page = self._get_page("user-1")

        self.assertEqual(page["total_count"], 12)
        self.assertTrue(all(review["author_id"] == "user-1" for review in page["reviews"]))

    def test_malformed_cursor_is_rejected(self):
        self._add_reviews("user-1", 12)
        page = self._get_page()
        with self.assertRaises(firebase_service.InvalidReviewCursor):
            self._get_page(cursor="%%%")

    def test_modified_timestamp_is_rejected(self):
        self._add_reviews("user-1", 12)
        cursor = self._get_page()["next_cursor"]
        payload = self._cursor_payload(cursor)
        payload["created_at"]["value"] = "2025-12-31T00:00:00Z"

        with self.assertRaises(firebase_service.InvalidReviewCursor):
            self._get_page(cursor=self._replace_cursor_payload(cursor, payload))

    def test_invalid_cursor_version_is_rejected_even_with_valid_signature(self):
        self._add_reviews("user-1", 12)
        payload = self._cursor_payload(self._get_page()["next_cursor"])
        payload["version"] = 2

        with self.assertRaises(firebase_service.InvalidReviewCursor):
            self._get_page(cursor=self._sign_cursor_payload(payload))

    def test_modified_document_id_is_rejected(self):
        self._add_reviews("user-1", 12)
        cursor = self._get_page()["next_cursor"]
        payload = self._cursor_payload(cursor)
        payload["document_id"] = "another-valid-document"

        with self.assertRaises(firebase_service.InvalidReviewCursor):
            self._get_page(cursor=self._replace_cursor_payload(cursor, payload))

    def test_modified_user_id_is_rejected(self):
        self._add_reviews("user-1", 12)
        cursor = self._get_page()["next_cursor"]
        payload = self._cursor_payload(cursor)
        payload["user_id"] = "user-2"

        with self.assertRaises(firebase_service.InvalidReviewCursor):
            self._get_page(cursor=self._replace_cursor_payload(cursor, payload))

    def test_validly_signed_cursor_for_another_user_is_rejected(self):
        self._add_reviews("user-2", 12)
        cursor = self._get_page("user-2")["next_cursor"]

        with self.assertRaises(firebase_service.InvalidReviewCursor):
            self._get_page("user-1", cursor)

    def test_invalid_signature_is_rejected(self):
        self._add_reviews("user-1", 12)
        cursor = self._get_page()["next_cursor"]
        encoded_payload, signature = cursor.split(".", 1)
        modified_signature = ("A" if signature[0] != "A" else "B") + signature[1:]

        with self.assertRaises(firebase_service.InvalidReviewCursor):
            self._get_page(cursor=f"{encoded_payload}.{modified_signature}")

    def test_missing_secret_fails_with_configuration_error(self):
        with patch.dict(os.environ, {"REVIEW_CURSOR_SECRET": ""}):
            with self.assertRaisesRegex(
                firebase_service.ReviewCursorConfigurationError,
                "REVIEW_CURSOR_SECRET",
            ):
                self._get_page()

    def test_deleted_cursor_document_does_not_break_next_page(self):
        self._add_reviews("user-1", 12)
        first_page = self._get_page()
        cursor_data = self._cursor_payload(first_page["next_cursor"])
        self.database.collection(firebase_service.CODE_REVIEWS).documents.pop(
            cursor_data["document_id"]
        )

        second_page = self._get_page(cursor=first_page["next_cursor"])

        self.assertEqual(
            [review["id"] for review in second_page["reviews"]],
            ["user-1-review-01", "user-1-review-00"],
        )
        self.assertEqual(second_page["total_count"], 11)

    def test_paginated_endpoint_returns_envelope_and_rejects_uid_spoofing(self):
        self._add_reviews("user-1", 2)
        with (
            patch.object(reviews_router, "firebase_available", return_value=True),
            patch.object(firebase_service, "get_db", return_value=self.database),
            patch(
                "app.auth.firebase_auth.verify_id_token",
                return_value={"uid": "user-1"},
            ),
        ):
            with TestClient(app) as client:
                response = client.get(
                    "/api/reviews/page?user_id=user-1",
                    headers={"Authorization": "Bearer valid-token"},
                )
                spoofed = client.get(
                    "/api/reviews/page?user_id=user-2",
                    headers={"Authorization": "Bearer valid-token"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"reviews", "next_cursor", "total_count"})
        self.assertEqual(response.json()["total_count"], 2)
        self.assertEqual(spoofed.status_code, 403)


if __name__ == "__main__":
    unittest.main()
