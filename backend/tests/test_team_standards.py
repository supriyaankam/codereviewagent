import unittest

from app.services.code_analyzer import analyze_code
from app.services.team_standard_analyzer import (
    analyze_team_standards,
    redact_credential_values,
)


class TeamStandardAnalyzerTests(unittest.TestCase):
    def credential_standard(self, enabled=True):
        return [{
            "id": "credential-rule",
            "title": "Never hardcode credentials",
            "description": "API keys, passwords, secrets, and tokens must not be embedded in source code.",
            "category": "security",
            "severity": "critical",
            "recommended_solution": "Use an environment variable or secrets manager.",
            "enabled": enabled,
        }]

    def test_hardcoded_credential_is_reported_without_exposing_value(self):
        code = 'api_key = "sk_live_SUPERSECRET123"\n'
        findings = analyze_team_standards("python", code, self.credential_standard())
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0]["team_standard"])
        self.assertEqual(findings[0]["line"], 1)
        self.assertNotIn("sk_live_SUPERSECRET123", findings[0]["description"])
        self.assertNotIn("sk_live_SUPERSECRET123", findings[0]["recommendation"])

    def test_environment_variable_credential_is_not_a_violation(self):
        code = 'api_key = os.getenv("API_KEY")\n'
        findings = analyze_team_standards("python", code, self.credential_standard())
        self.assertEqual(findings, [])

    def test_environment_variable_name_is_not_reported_or_redacted(self):
        code = "api_key = API_KEY\n"
        findings = analyze_team_standards("python", code, self.credential_standard())
        self.assertEqual(findings, [])
        self.assertEqual(redact_credential_values(code), code)

    def test_redacts_prefixed_keys_passwords_database_urls_and_bearer_tokens(self):
        values = [
            "gsk_synthetic_secret_123456789",
            "api_key_secret_value_12345",
            "secret_key_value_12345",
            "db-password-synthetic",
            "db-user-synthetic",
            "eyJ.synthetic.bearer.token",
            "refresh-token-synthetic",
        ]
        text = "\n".join([
            'GROQ_API_KEY="gsk_synthetic_secret_123456789"',
            'API_KEY="api_key_secret_value_12345"',
            'SECRET_KEY="secret_key_value_12345"',
            'DATABASE_URL="postgresql://db-user-synthetic:db-password-synthetic@db.example/app"',
            "Authorization: Bearer eyJ.synthetic.bearer.token",
            "refresh_token=refresh-token-synthetic",
            "https://url-user-synthetic:url-password-synthetic@example.test/path",
        ])

        redacted = redact_credential_values(text)

        for value in [*values, "url-user-synthetic", "url-password-synthetic"]:
            self.assertNotIn(value, redacted)
        self.assertIn("GROQ_API_KEY", redacted)
        self.assertIn("Bearer ********", redacted)

    def test_fake_api_key_is_detected_without_returning_the_value(self):
        secret = "sk_live_FAKE_KEY_1234567890"
        code = f'const apiKey = "{secret}";\n'
        findings = analyze_team_standards("javascript", code, self.credential_standard())
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["line"], 1)
        self.assertEqual(findings[0]["standard_name"], "Never hardcode credentials")
        self.assertNotIn(secret, str(findings[0]))
        self.assertNotIn(secret, redact_credential_values(code))

    def test_disabled_standard_is_not_reported(self):
        code = 'password = "not-safe-placeholder"\n'
        findings = analyze_team_standards("python", code, self.credential_standard(enabled=False))
        self.assertEqual(findings, [])

    def test_multiple_files_are_reported_at_their_exact_lines(self):
        code = 'print("safe")\npassword = "value123"\n'
        findings = analyze_team_standards("python", code, self.credential_standard())
        self.assertEqual([finding["line"] for finding in findings], [2])

    def test_placeholder_value_is_not_reported_as_hardcoded_secret(self):
        code = 'api_key = "example-api-key-placeholder"\n'
        findings = analyze_team_standards("python", code, self.credential_standard())
        self.assertEqual(findings, [])

    def test_database_url_standard_does_not_claim_credential_findings(self):
        database_standard = [{
            "id": "database-url-rule",
            "title": "Avoid hardcoded database URLs",
            "description": "Load database URLs from configuration.",
            "category": "security",
            "enabled": True,
        }]
        code = (
            'api_key = "sk_live_FAKE_KEY_1234567890"\n'
            'password = "fake-password-value"\n'
        )

        findings = analyze_team_standards("python", code, database_standard)

        self.assertEqual(findings, [])

    def test_credentials_and_database_urls_match_their_own_standards(self):
        standards = self.credential_standard() + [{
            "id": "database-url-rule",
            "title": "Avoid hardcoded database URLs",
            "description": "Load database URLs from configuration.",
            "category": "security",
            "enabled": True,
        }]
        code = (
            'api_key = "sk_live_FAKE_KEY_1234567890"\n'
            'password = "fake-password-value"\n'
            'database_url = "postgresql://user:pass@db.example/app"\n'
            'database_url = "not-a-database-url"\n'
        )

        findings = analyze_team_standards("python", code, standards)

        self.assertEqual(
            [(finding["line"], finding["standard_name"]) for finding in findings],
            [
                (1, "Never hardcode credentials"),
                (2, "Never hardcode credentials"),
                (3, "Avoid hardcoded database URLs"),
            ],
        )

    def test_duplicate_credential_standards_emit_one_finding_per_location(self):
        standard = self.credential_standard()[0]
        duplicate = {**standard, "id": "duplicate-credential-rule"}

        findings = analyze_team_standards(
            "python",
            'api_key = "sk_live_FAKE_KEY_1234567890"\n',
            [duplicate, standard],
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["standard_name"], "Never hardcode credentials")

    def test_code_analysis_does_not_attribute_credentials_to_database_standard(self):
        database_standard = [{
            "id": "database-url-rule",
            "title": "Avoid hardcoded database URLs",
            "description": "Load database URLs from configuration.",
            "category": "security",
            "enabled": True,
        }]

        result = analyze_code(
            "python",
            'api_key = "sk_live_FAKE_KEY_1234567890"\n',
            team_standards=database_standard,
        )

        team_findings = [issue for issue in result["issues"] if issue.get("team_standard")]
        self.assertEqual(team_findings, [])
        self.assertTrue(any("API key" in issue["title"] for issue in result["issues"]))

    def test_team_finding_is_added_to_ordinary_analysis(self):
        code = 'password = "hardcoded123"\n'
        result = analyze_code(
            "python",
            code,
            team_standards=self.credential_standard(),
        )
        team = [issue for issue in result["issues"] if issue.get("team_standard")]
        self.assertTrue(team)
        self.assertEqual(team[0]["standard_name"], "Never hardcode credentials")

    def test_non_python_analysis_includes_team_standard_findings(self):
        result = analyze_code(
            "javascript",
            'const apiKey = "sk_live_FAKE_KEY_1234567890";\n',
            team_standards=self.credential_standard(),
        )
        self.assertTrue(result["supported"])
        self.assertIn("syntax validation", result["message"])
        self.assertEqual(len(result["issues"]), 1)
        self.assertTrue(result["issues"][0]["team_standard"])


if __name__ == "__main__":
    unittest.main()
