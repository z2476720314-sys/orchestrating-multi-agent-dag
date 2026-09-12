import os
import unittest

from observer.redaction import (
    HIDDEN_TEXT,
    canonical_path_key,
    sanitize_identifier,
    sanitize_model_name,
    sanitize_text,
    sanitize_tool_name,
)


class RedactionTests(unittest.TestCase):
    def test_hides_bearer_credentials(self) -> None:
        self.assertEqual(HIDDEN_TEXT, sanitize_text("Authorization: Bearer abc123"))

    def test_hides_reasoning_content(self) -> None:
        self.assertEqual(HIDDEN_TEXT, sanitize_text("dsh: reasoning: private"))

    def test_hides_prompt_and_tool_payload_fields(self) -> None:
        unsafe_values = (
            "system prompt: do not reveal",
            "developer instructions=hidden",
            'tool_args={"path": "secret"}',
            "stdout: complete raw output",
            "cookie=session-value",
            "https://example.test/?access_token=abc123",
            r"C:\private\.credentials.json",
            "OPENAI_API_KEY=abc123",
        )

        for value in unsafe_values:
            with self.subTest(value=value):
                self.assertEqual(HIDDEN_TEXT, sanitize_text(value))

    def test_secret_labels_followed_by_whitespace_are_hidden(self) -> None:
        for value in ("password abc", "passwd abc", "token abc", "api key abc"):
            with self.subTest(value=value):
                self.assertEqual(HIDDEN_TEXT, sanitize_text(value))

    def test_keeps_short_public_status_text(self) -> None:
        self.assertEqual("正在解析协调状态", sanitize_text("  正在解析\n协调状态  "))

    def test_limits_public_text(self) -> None:
        self.assertEqual("abc…", sanitize_text("abcdef", limit=4))

    def test_invalid_or_empty_text_is_hidden(self) -> None:
        self.assertEqual(HIDDEN_TEXT, sanitize_text(""))
        self.assertEqual(HIDDEN_TEXT, sanitize_text("visible", limit=0))

    def test_identifier_model_and_tool_names_use_strict_value_formats(self) -> None:
        self.assertEqual("session-01a0", sanitize_identifier("session-01a0"))
        self.assertEqual("workbuddy/deepseek-v4.1-flash", sanitize_model_name("workbuddy/deepseek-v4.1-flash"))
        self.assertEqual("mcp__server__read", sanitize_tool_name("mcp__server__read"))

        unsafe_values = (
            "id with spaces",
            "../../escape",
            "model\nprivate",
            "tool(args)",
            "开发者指令",
        )
        for value in unsafe_values:
            with self.subTest(value=value):
                self.assertEqual(HIDDEN_TEXT, sanitize_identifier(value))
                self.assertEqual(HIDDEN_TEXT, sanitize_model_name(value))
                self.assertEqual(HIDDEN_TEXT, sanitize_tool_name(value))

    @unittest.skipUnless(os.name == "nt", "Windows extended paths are platform-specific")
    def test_canonical_path_treats_windows_extended_prefix_as_equivalent(self) -> None:
        regular = r"C:\workspace\project"
        extended = r"\\?\C:\workspace\project\."

        self.assertEqual(canonical_path_key(regular), canonical_path_key(extended))


if __name__ == "__main__":
    unittest.main()
