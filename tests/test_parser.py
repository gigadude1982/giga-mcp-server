from __future__ import annotations

from datetime import datetime, timezone

from giga_mcp_server.parser.rule_based import RuleBasedParser


class TestRuleBasedParser:
    def setup_method(self) -> None:
        self.parser = RuleBasedParser()

    def test_basic_idea(self) -> None:
        idea = self.parser.parse("Build a dashboard for tracking user signups", "alice")
        assert idea.summary == "Build a dashboard for tracking user signups"
        assert idea.issue_type == "Story"
        assert idea.priority == ""  # No priority keyword → empty (config default applied later)
        assert idea.sender == "alice"

    def test_high_priority_detection(self) -> None:
        idea = self.parser.parse("urgent: fix login bug on mobile app", "bob")
        assert idea.priority == "High"
        assert idea.issue_type == "Bug"

    def test_critical_priority(self) -> None:
        idea = self.parser.parse("critical production outage in payments", "alice")
        assert idea.priority == "Highest"

    def test_low_priority(self) -> None:
        idea = self.parser.parse("low priority: update the favicon", "alice")
        assert idea.priority == "Low"

    def test_bug_detection(self) -> None:
        idea = self.parser.parse("There's a crash when uploading large files", "bob")
        assert idea.issue_type == "Bug"

    def test_task_detection(self) -> None:
        idea = self.parser.parse("task: cleanup old migration files", "alice")
        assert idea.issue_type == "Task"

    def test_hashtag_labels(self) -> None:
        idea = self.parser.parse("Add search to admin panel #backend #search", "alice")
        assert "backend" in idea.labels
        assert "search" in idea.labels

    def test_hashtags_stripped_from_summary(self) -> None:
        idea = self.parser.parse("Add search #backend #search", "alice")
        assert "#" not in idea.summary

    def test_long_message_truncated(self) -> None:
        long_msg = "A" * 200
        idea = self.parser.parse(long_msg, "alice")
        assert len(idea.summary) <= 120

    def test_multiline_first_sentence(self) -> None:
        idea = self.parser.parse("First sentence here.\nMore details below.", "alice")
        assert idea.summary == "First sentence here"

    def test_description_includes_attribution(self) -> None:
        idea = self.parser.parse("Some idea", "bob")
        assert "bob" in idea.description

    def test_empty_message(self) -> None:
        idea = self.parser.parse("", "alice")
        assert idea.summary == "Empty message"

    def test_timestamp_preserved(self) -> None:
        ts = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)
        idea = self.parser.parse("An idea", "alice", ts)
        assert idea.timestamp == ts

    def test_story_keyword_detection(self) -> None:
        idea = self.parser.parse("implement a new notification system", "alice")
        assert idea.issue_type == "Story"

    def test_default_issue_type_is_empty(self) -> None:
        idea = self.parser.parse("something without keywords", "alice")
        assert idea.issue_type == ""  # No keyword match → empty (config default applied later)
