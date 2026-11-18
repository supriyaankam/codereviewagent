import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from groq import RateLimitError

from app.services import auto_fix_service


class AutoFixRateLimitTests(unittest.TestCase):
    source = "value = 1\n"
    issue = {"title": "Unsafe value", "severity": "warning"}

    @staticmethod
    def rate_limit_error(message="", headers=None):
        response = httpx.Response(
            429,
            headers=headers or {},
            request=httpx.Request("POST", "https://api.groq.com"),
            json={"error": {"message": message}},
        )
        return RateLimitError(
            "rate limited",
            response=response,
            body=response.json(),
        )

    def test_http_429_reports_rpm_and_retry_after(self):
        client = Mock()
        client.chat.completions.create.side_effect = self.rate_limit_error(
            "Rate limit reached for requests per minute (RPM).",
            {"retry-after": "30"},
        )

        with patch.object(auto_fix_service, "_get_client", return_value=client):
            result = auto_fix_service.auto_fix_code(
                self.source, "python", [self.issue]
            )

        self.assertFalse(result["ai_available"])
        self.assertIn("requests per minute (RPM)", result["error"])
        self.assertIn("Retry in about 30 seconds", result["error"])
        self.assertIn("No automatic retry was made", result["error"])
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_reset_header_is_used_when_retry_after_is_missing(self):
        client = Mock()
        client.chat.completions.create.side_effect = self.rate_limit_error(
            "Rate limit reached for tokens per minute (TPM).",
            {"x-ratelimit-reset-tokens": "1m2.5s"},
        )

        with patch.object(auto_fix_service, "_get_client", return_value=client):
            result = auto_fix_service.auto_fix_code(
                self.source, "python", [self.issue]
            )

        self.assertIn("tokens per minute (TPM)", result["error"])
        self.assertIn("Retry in about 63 seconds", result["error"])

    def test_missing_retry_headers_gives_quota_guidance_without_retry(self):
        client = Mock()
        client.chat.completions.create.side_effect = self.rate_limit_error(
            "Rate limit reached."
        )

        with patch.object(auto_fix_service, "_get_client", return_value=client):
            result = auto_fix_service.auto_fix_code(
                self.source, "python", [self.issue]
            )

        self.assertIn("Groq did not provide a retry time", result["error"])
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_groq_client_disables_sdk_automatic_retries(self):
        with (
            patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}),
            patch.object(auto_fix_service, "Groq") as groq_client,
        ):
            auto_fix_service._get_client()

        groq_client.assert_called_once_with(api_key="test-key", max_retries=0)

    def test_successful_autofix_returns_proposed_code(self):
        corrected = "value = 2\n"
        response = SimpleNamespace(choices=[
            SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                "corrected_code": corrected,
                "changes": [{"issue": "Unsafe value", "change": "Updated value."}],
                "summary": "Updated the value.",
            }))),
        ])
        client = Mock()
        client.chat.completions.create.return_value = response

        with patch.object(auto_fix_service, "_get_client", return_value=client):
            result = auto_fix_service.auto_fix_code(
                self.source, "python", [self.issue]
            )

        self.assertTrue(result["ai_available"])
        self.assertEqual(result["fixed_code"], corrected)
        client.chat.completions.create.assert_called_once()
        self.assertEqual(
            client.chat.completions.create.call_args.kwargs["model"],
            auto_fix_service.DEFAULT_FIX_MODEL,
        )
        self.assertEqual(
            client.chat.completions.create.call_args.kwargs["max_tokens"],
            auto_fix_service.MAX_FIX_COMPLETION_TOKENS,
        )


if __name__ == "__main__":
    unittest.main()
