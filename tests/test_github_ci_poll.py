"""Tests for the CI-polling race fix in GitHubClient.

After a fix commit is pushed via the Git Data API, GitHub's PR.head.sha lags
the ref update by a few seconds. Polling the PR (unpinned) would then read the
*previous* commit's failed checks and count the fix as failed instantly. The
fix: pin polling to the SHA we just pushed, and require checks to actually
register before trusting a verdict on a repo we know has CI.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from giga_mcp_server.pipeline.github_tools import (
    ChecksStatus,
    GitHubClient,
    _distill_log,
)


# A representative noisy Jest + tsc CI log (ISO-timestamped, full of
# node_modules stack frames) like the one PUNCH-3 produced.
_RAW_LOG = """\
2026-05-31T01:15:20.7Z FAIL src/components/Footer.test.tsx
2026-05-31T01:15:20.7Z   ● Footer › displays the version
2026-05-31T01:15:20.7Z     TestingLibraryElementError: Found multiple elements with the text: v1.2.3
2026-05-31T01:15:20.7Z       <div>v1.2.3</div>
2026-05-31T01:15:20.7Z       at Object.getElementError (node_modules/@testing-library/dom/dist/config.js:37:19)
2026-05-31T01:15:20.7Z       at getByText (node_modules/@testing-library/dom/dist/query-helpers.js:20:35)
2026-05-31T01:15:20.7Z     Expected: 1
2026-05-31T01:15:20.7Z     Received: 2
2026-05-31T01:15:21.0Z src/App.tsx(10,7): error TS2304: Cannot find name 'foo'.
2026-05-31T01:15:22.5Z Tests:       7 failed, 37 passed, 44 total
"""


def test_distill_log_keeps_signal_drops_noise():
    out = _distill_log(_RAW_LOG)
    # The actionable lines survive…
    assert "Found multiple elements with the text: v1.2.3" in out
    assert "error TS2304: Cannot find name 'foo'." in out
    assert "Expected: 1" in out and "Received: 2" in out
    assert "Tests:       7 failed, 37 passed, 44 total" in out
    # …the noise is gone.
    assert "node_modules" not in out
    assert "2026-05-31T" not in out  # timestamps stripped


def _client() -> GitHubClient:
    return GitHubClient(token="t", repo="o/r")


@pytest.mark.asyncio
async def test_require_checks_does_not_short_circuit_to_none():
    # No checks registered yet for the new SHA twice, then a real verdict.
    # With require_checks=True the "none" shortcut must be suppressed so the
    # fix gets a fair CI run instead of being judged instantly.
    gh = _client()
    gh.get_pr_status = AsyncMock(
        side_effect=[
            ChecksStatus(state="pending"),  # no runs yet
            ChecksStatus(state="pending"),  # still spinning up
            ChecksStatus(state="success", passed=["build"]),
        ]
    )
    res = await gh.poll_pr_until_complete(
        7, timeout=100, interval=0, no_checks_grace=0, head_sha="newsha", require_checks=True
    )
    assert res.state == "success"
    assert gh.get_pr_status.await_count == 3
    # Polling must be pinned to the pushed SHA, not the lagging PR head.
    assert gh.get_pr_status.await_args.kwargs["head_sha"] == "newsha"


@pytest.mark.asyncio
async def test_no_checks_falls_back_to_none_when_not_required():
    # Initial poll on a repo with no PR CI: never any checks → "none" so the
    # run doesn't block to the timeout.
    gh = _client()
    gh.get_pr_status = AsyncMock(return_value=ChecksStatus(state="pending"))
    res = await gh.poll_pr_until_complete(
        7, timeout=100, interval=0, no_checks_grace=0, require_checks=False
    )
    assert res.state == "none"


@pytest.mark.asyncio
@respx.mock
async def test_get_pr_status_pins_head_sha_and_skips_pr_fetch():
    gh = _client()
    pulls = respx.get("https://api.github.com/repos/o/r/pulls/7").mock(
        return_value=httpx.Response(500)  # must NOT be called when head_sha given
    )
    checks = respx.get(
        "https://api.github.com/repos/o/r/commits/deadbeef/check-runs"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"check_runs": [
                {"name": "build", "status": "completed", "conclusion": "failure"},
            ]},
        )
    )
    res = await gh.get_pr_status(7, head_sha="deadbeef")
    assert res.state == "failure"
    assert checks.called
    assert not pulls.called  # pinned SHA avoids the lagging PR.head.sha read
