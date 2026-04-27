from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()

_DEFAULTS: dict[str, Any] = {
    "language": "python",
    "test_framework": "pytest",
    "test_command": "pytest",
    "coding_standards": (
        "Follow PEP 8. Use type hints. Write docstrings for public functions. "
        "Prefer dataclasses over dicts for structured data. Use structlog for logging."
    ),
    "source_dirs": ["src"],
    "test_dirs": ["tests"],
    "max_retries_per_stage": 3,
    "human_gate_after_planner": True,
    "branch_prefix": "auto/",
    "write_tests": True,
    "pipeline_model": None,  # None = use server default (claude-sonnet-4-6)
}


@dataclass
class RepoConfig:
    language: str = "python"
    test_framework: str = "pytest"
    test_command: str = "pytest"
    coding_standards: str = ""
    source_dirs: list[str] = field(default_factory=lambda: ["src"])
    test_dirs: list[str] = field(default_factory=lambda: ["tests"])
    max_retries_per_stage: int = 3
    human_gate_after_planner: bool = True
    branch_prefix: str = "auto/"
    write_tests: bool = True
    pipeline_model: str | None = None  # overrides _PIPELINE_MODEL in agent_runner

    @classmethod
    def from_dict(cls, data: dict[str, Any], default_max_retries: int = 3) -> RepoConfig:
        defaults = {**_DEFAULTS, "max_retries_per_stage": default_max_retries}
        merged = {**defaults, **data}
        return cls(
            language=merged["language"],
            test_framework=merged["test_framework"],
            test_command=merged["test_command"],
            coding_standards=merged["coding_standards"],
            source_dirs=merged["source_dirs"],
            test_dirs=merged["test_dirs"],
            max_retries_per_stage=merged["max_retries_per_stage"],
            human_gate_after_planner=merged["human_gate_after_planner"],
            branch_prefix=merged["branch_prefix"],
            write_tests=merged["write_tests"],
            pipeline_model=merged["pipeline_model"],
        )

    @classmethod
    def defaults(cls, max_retries: int = 3) -> RepoConfig:
        return cls.from_dict({}, default_max_retries=max_retries)


async def load_repo_config(
    github_client: Any, repo: str, base_branch: str, default_max_retries: int = 3
) -> RepoConfig:
    """Load .giga-pipeline.json from the repo root. Falls back to defaults.

    default_max_retries is used as the fallback when the repo config does not
    specify max_retries_per_stage (maps to GIGA_PIPELINE_MAX_RETRIES).
    """
    try:
        content = await github_client.get_file(".giga-pipeline.json", base_branch)
        data = json.loads(content)
        logger.info("repo_config_loaded", repo=repo)
        return RepoConfig.from_dict(data, default_max_retries=default_max_retries)
    except Exception:
        logger.info("repo_config_not_found", repo=repo, hint="using defaults")
        return RepoConfig.defaults(max_retries=default_max_retries)
