"""Tests for the Markdown -> JIRA wiki markup converter.

Regression guard: JIRA's v2 API treats descriptions as wiki markup, so Markdown
headings (`## x`) were being read as nested ordered lists and rendered as a
mangled `1. a.` structure. The converter must emit real wiki headings/lists.
"""

from giga_mcp_server.jira.client import markdown_to_jira_wiki


def test_headings_become_wiki_headings_not_ordered_lists():
    md = "## Summary\nReplace the button.\n\n### Requirements\n- one\n- two"
    out = markdown_to_jira_wiki(md)
    assert "h2. Summary" in out
    assert "h3. Requirements" in out
    # the old bug: '##' must NOT survive as a markdown heading
    assert "## Summary" not in out


def test_bullets_and_nested_bullets():
    out = markdown_to_jira_wiki("- top\n  - nested")
    lines = out.splitlines()
    assert lines[0] == "* top"
    assert lines[1] == "** nested"


def test_numbered_list_becomes_hash():
    out = markdown_to_jira_wiki("1. first\n2. second")
    assert out.splitlines() == ["# first", "# second"]


def test_inline_bold_code_and_links():
    out = markdown_to_jira_wiki("Use **bold**, `code`, and [docs](https://x.io).")
    assert "*bold*" in out
    assert "{{code}}" in out
    assert "[docs|https://x.io]" in out


def test_fenced_code_block_passthrough():
    md = "```ts\nconst a = 1; // **not bold**\n```"
    out = markdown_to_jira_wiki(md)
    assert "{code:ts}" in out and "{code}" in out
    # content inside the fence is untouched (no bold conversion)
    assert "const a = 1; // **not bold**" in out


def test_empty_passthrough():
    assert markdown_to_jira_wiki("") == ""
