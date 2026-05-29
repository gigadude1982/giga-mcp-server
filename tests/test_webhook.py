"""Tests for the GitHub webhook helpers (HMAC verification + merge detection)."""

import hashlib
import hmac
import json

from giga_mcp_server.server import _merged_pr_number, _verify_github_signature

SECRET = "test-secret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestVerifySignature:
    def test_accepts_a_valid_signature(self):
        body = b'{"hello":"world"}'
        assert _verify_github_signature(SECRET, body, _sign(body)) is True

    def test_rejects_a_wrong_signature(self):
        body = b'{"hello":"world"}'
        assert _verify_github_signature(SECRET, body, _sign(body, "other")) is False

    def test_rejects_missing_secret_or_header(self):
        body = b"{}"
        assert _verify_github_signature("", body, _sign(body)) is False
        assert _verify_github_signature(SECRET, body, "") is False


def _payload(action="closed", merged=True, base="main", repo="o/r", number=7):
    return {
        "action": action,
        "pull_request": {"merged": merged, "base": {"ref": base}, "number": number},
        "repository": {"full_name": repo},
    }


class TestMergedPrNumber:
    def test_returns_number_for_a_merge_into_base(self):
        assert _merged_pr_number("pull_request", _payload(), "main", "o/r") == 7

    def test_ignores_non_merge_close(self):
        assert _merged_pr_number("pull_request", _payload(merged=False), "main", "o/r") is None

    def test_ignores_non_pull_request_events(self):
        assert _merged_pr_number("push", _payload(), "main", "o/r") is None

    def test_ignores_merge_into_a_different_base(self):
        assert _merged_pr_number("pull_request", _payload(base="dev"), "main", "o/r") is None

    def test_ignores_a_different_repo(self):
        assert _merged_pr_number("pull_request", _payload(repo="x/y"), "main", "o/r") is None

    def test_round_trips_through_json_like_a_real_delivery(self):
        raw = json.dumps(_payload(number=42)).encode()
        assert _merged_pr_number("pull_request", json.loads(raw), "main", "o/r") == 42
