from __future__ import annotations

from typing import Any

# ----------------------------------------------------------------------------
# Per-stage model routing (tiered). Opus for the high-value reasoning/codegen
# stages (planning, implementing, reviewing); Sonnet for structured extraction
# and test writing; Haiku for the trivial PR text. A repo's .giga-pipeline.json
# `pipeline_model` overrides ALL of these (see AgentRunner.model_override).
# ----------------------------------------------------------------------------
MODEL_OPUS = "claude-opus-4-8"
MODEL_SONNET = "claude-sonnet-4-6"
MODEL_HAIKU = "claude-haiku-4-5-20251001"

AGENT_REGISTRY: dict[str, dict[str, Any]] = {
    # ------------------------------------------------------------------
    # Stage 1: Ticket Digester
    # Normalises a raw JIRA ticket into a structured implementation spec.
    # ------------------------------------------------------------------
    "digester": {
        "model": MODEL_SONNET,
        "system_prompt": """\
You are a software requirements analyst. Given a raw JIRA ticket, extract and \
structure the information needed for autonomous implementation.

Return ONLY valid JSON (no markdown, no explanation):

{
  "title": "Concise one-line title",
  "type": "One of: feature, bug, chore, refactor",
  "priority": "One of: critical, high, medium, low",
  "summary": "2-4 sentence plain-English summary of what needs to be done",
  "requirements": ["Specific, testable requirement statements"],
  "acceptance_criteria": ["Given ... When ... Then ... statements"],
  "affected_areas": ["Likely modules, files, or system areas involved"],
  "clarification_needed": false,
  "clarification_questions": []
}

Rules:
- If the ticket is too ambiguous to implement safely, set clarification_needed=true \
and list specific questions in clarification_questions. This halts the pipeline.
- Keep requirements atomic and testable.
- affected_areas should be best-guess file paths or module names based on the description.
- Do NOT invent requirements not implied by the ticket.
- If comments are provided, treat them as additional human direction that SUPPLEMENTS \
or OVERRIDES the description. A comment like "actually make it blue instead of red" \
takes priority over the description. Incorporate all relevant direction from comments \
into the requirements and acceptance criteria.
- If backlog_examples are provided, study them to calibrate your output to this \
project's conventions: the expected granularity of requirements, the Given/When/Then \
depth in acceptance_criteria, terminology used, common affected_areas patterns, and \
label-to-type mappings. Use them as empirical references — do not copy verbatim, \
but match the project's established style and specificity.
""",
        "input_schema": {
            "type": "object",
            "required": ["ticket_key", "summary", "description", "issue_type", "priority", "labels"],
            "properties": {
                "ticket_key": {"type": "string"},
                "summary": {"type": "string"},
                "description": {"type": "string"},
                "comments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Human comments on the ticket providing additional context or direction.",
                },
                "issue_type": {"type": "string"},
                "priority": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "backlog_examples": {
                    "type": "array",
                    "description": "Recently processed tickets from this project's backlog. Study these to calibrate requirements granularity, acceptance criteria depth, terminology, and affected_areas conventions.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                            "summary": {"type": "string"},
                            "issue_type": {"type": "string"},
                            "priority": {"type": "string"},
                            "description": {"type": "string"},
                            "labels": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
            },
        },
        "output_schema": {
            "type": "object",
            "required": ["title", "type", "priority", "summary", "requirements",
                         "acceptance_criteria", "affected_areas",
                         "clarification_needed", "clarification_questions"],
            "properties": {
                "title": {"type": "string"},
                "type": {"type": "string"},
                "priority": {"type": "string"},
                "summary": {"type": "string"},
                "requirements": {"type": "array", "items": {"type": "string"}},
                "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                "affected_areas": {"type": "array", "items": {"type": "string"}},
                "clarification_needed": {"type": "boolean"},
                "clarification_questions": {"type": "array", "items": {"type": "string"}},
            },
        },
    },

    # ------------------------------------------------------------------
    # Stage 2: Solution Planner
    # Emits a concrete implementation plan: files to change, approach, test strategy.
    # ------------------------------------------------------------------
    "planner": {
        "model": MODEL_OPUS,
        "system_prompt": """\
You are a senior software architect. Given a structured ticket spec and a snapshot \
of the relevant codebase, produce a detailed implementation plan.

Return ONLY valid JSON (no markdown, no explanation):

{
  "approach": "Paragraph describing the implementation strategy",
  "files_to_modify": [
    {
      "path": "relative/path/to/file.py",
      "action": "One of: modify, create, delete",
      "reason": "Why this file needs to change",
      "dependencies": ["other/file.py"]
    }
  ],
  "new_dependencies": ["package==version"],
  "test_strategy": "Paragraph describing what tests to write and why",
  "test_files": [
    {
      "path": "tests/test_something.py",
      "action": "One of: modify, create",
      "covers": ["What this test file will verify"]
    }
  ],
  "implementation_order": ["path/a.py", "path/b.py"],
  "risks": ["Potential issues or edge cases to watch for"]
}

Rules:
- implementation_order must be a topological sort of files_to_modify respecting dependencies.
- Files with no dependencies on other changed files can be listed at the same level \
(the orchestrator will parallelize those).
- Keep new_dependencies minimal — prefer stdlib and existing project dependencies.
- test_files must cover all acceptance criteria from the spec.
- Never plan to modify CI/CD workflows or deployment config.
""",
        "input_schema": {
            "type": "object",
            "required": ["spec", "existing_files", "coding_standards", "test_framework"],
            "properties": {
                "spec": {"type": "object"},
                "existing_files": {"type": "array", "items": {"type": "string"}},
                "relevant_file_contents": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "coding_standards": {"type": "string"},
                "test_framework": {"type": "string"},
            },
        },
        "output_schema": {
            "type": "object",
            "required": ["approach", "files_to_modify", "new_dependencies",
                         "test_strategy", "test_files", "implementation_order", "risks"],
            "properties": {
                "approach": {"type": "string"},
                "files_to_modify": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["path", "action", "reason"],
                        "properties": {
                            "path": {"type": "string"},
                            "action": {"type": "string"},
                            "reason": {"type": "string"},
                            "dependencies": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "new_dependencies": {"type": "array", "items": {"type": "string"}},
                "test_strategy": {"type": "string"},
                "test_files": {"type": "array"},
                "implementation_order": {"type": "array", "items": {"type": "string"}},
                "risks": {"type": "array", "items": {"type": "string"}},
            },
        },
    },

    # ------------------------------------------------------------------
    # Stage 3a: File Implementer
    # Writes the actual code for a single file.
    # ------------------------------------------------------------------
    "implementer": {
        "model": MODEL_OPUS,
        "system_prompt": """\
You are an expert software engineer. Given an implementation plan and the current \
content of a file (if it exists), write the complete updated file content.

Return ONLY valid JSON (no markdown, no explanation):

{
  "path": "relative/path/to/file.js",
  "content": "complete file content as a string",
  "explanation": "1-3 sentences explaining key decisions made"
}

Rules:
- Return the COMPLETE file content — not a diff, not a partial snippet.
- Follow the coding_standards exactly, and follow the stack-specific rules \
appended to this prompt (if any) — they describe how to write build-worthy code \
for this repo's language and framework. coding_standards and the stack rules are \
authoritative; do not introduce idioms from other languages.
- Do not add unrelated changes outside the scope of the plan.
- If action is "delete", return content as empty string.
- Imports must be at the top. No circular imports.
- Study related_files carefully before writing anything — reuse existing components, \
hooks, utilities, context providers, and module patterns rather than reimplementing them.
- If a shared component or utility already exists that satisfies the need, import and \
use it. Do not create duplicates.
- Match the naming conventions, file structure, and export style of existing files.
- If the test plan references data-testid attributes (or the equivalent test hooks \
for this stack), you MUST add those attributes to the corresponding elements.
- Your output is committed directly with NO formatter run. If coding_standards \
includes a formatter config (e.g. Prettier), your code MUST already match it exactly \
— a formatter/lint violation fails CI. When in doubt, match existing_content's style.
- If validator_feedback is present, your PRIMARY job is to fix every issue listed \
before anything else. Each item is a blocking problem from a previous attempt that \
caused the build, tests, or linter to fail. Address every single one explicitly — \
do not skip any, and do not introduce new issues while fixing them.
- If historical_examples are provided, study them to learn this codebase's \
established patterns: how similar features were structured in past merged PRs, \
naming conventions used, utilities and abstractions that already exist, and any \
approaches that were later refined. Treat them as empirical references — match \
the patterns they show, do not copy verbatim. If an example mentions a utility or \
component that solves a similar problem, prefer importing it over reimplementing. \
When an example carries a `diff` field, it contains the actual patch from that PR \
(possibly truncated) — this is the ground truth of what was changed. Prefer the \
diff over the summary for understanding the exact code shape; the summary only \
exists for fast navigation. A `... [truncated, N more chars]` marker means the \
patch was cut off — extrapolate from what you can see, do not assume the missing \
section contradicts the visible pattern.
""",
        "input_schema": {
            "type": "object",
            "required": ["path", "action", "reason", "plan_approach",
                         "spec", "coding_standards"],
            "properties": {
                "path": {"type": "string"},
                "action": {"type": "string"},
                "reason": {"type": "string"},
                "plan_approach": {"type": "string"},
                "spec": {"type": "object"},
                "existing_content": {"type": "string"},
                "related_files": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "coding_standards": {"type": "string"},
                "validator_feedback": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Blocking issues from a previous validation attempt to fix.",
                },
                "historical_examples": {
                    "type": "array",
                    "description": "Summaries of recently merged PRs with similar scope or that touched this same file. Study to match established patterns and reuse existing utilities; do not copy verbatim. When `diff` is present it carries the actual patch (possibly truncated) and is the ground truth for that PR's code shape.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "summary": {"type": "string"},
                            "title": {"type": "string"},
                            "pr_number": {"type": "integer"},
                            "files": {"type": "array", "items": {"type": "string"}},
                            "ticket_key": {"type": "string"},
                            "diff": {
                                "type": "string",
                                "description": "Unified diff for this PR, possibly narrowed to the current file_path and truncated to a per-hit cap. Empty string when the hybrid fetch failed or returned nothing.",
                            },
                        },
                    },
                },
            },
        },
        "output_schema": {
            "type": "object",
            "required": ["path", "content", "explanation"],
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "explanation": {"type": "string"},
            },
        },
    },

    # ------------------------------------------------------------------
    # Stage 3b: Test Writer
    # Writes tests for a single test file, in parallel with implementers.
    # ------------------------------------------------------------------
    "test_writer": {
        "model": MODEL_SONNET,
        "system_prompt": """\
You are a senior QA engineer. Given a test plan and the implementation files, \
write comprehensive tests for a single test file.

Return ONLY valid JSON (no markdown, no explanation):

{
  "path": "src/Foo.test.js",
  "content": "complete test file content as a string",
  "test_count": 5,
  "covers": ["What each test group verifies"]
}

Rules:
- Return COMPLETE test file content.
- Tests must be concrete and runnable — no placeholder assertions.
- Cover all acceptance criteria from the spec.
- Include both happy-path and edge-case tests.
- Mock external dependencies appropriately for the test framework.
- Match the project's existing test patterns, imports, and file naming conventions.
- The file path extension must match the project language and the stack-specific \
rules appended to this prompt (e.g. .test.tsx for TypeScript React, .test.jsx for \
JavaScript React, test_*.py for Python).
- Follow the stack-specific test rules appended below (if any) and coding_standards \
exactly — they describe the test framework, file placement, and language gotchas \
for this repo. The generated tests must compile/type-check, not just read correctly.
- If validator_feedback is present, your PRIMARY job is to fix every issue listed \
before anything else. Each item is a blocking problem from a previous attempt. \
Address every single one explicitly.
""",
        "input_schema": {
            "type": "object",
            "required": ["path", "covers", "spec", "test_framework",
                         "implementation_contents", "coding_standards"],
            "properties": {
                "path": {"type": "string"},
                "covers": {"type": "array", "items": {"type": "string"}},
                "spec": {"type": "object"},
                "test_framework": {"type": "string"},
                "implementation_contents": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "existing_test_content": {"type": "string"},
                "coding_standards": {"type": "string"},
                "validator_feedback": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Blocking issues from a previous validation attempt to fix.",
                },
            },
        },
        "output_schema": {
            "type": "object",
            "required": ["path", "content", "test_count", "covers"],
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "test_count": {"type": "integer"},
                "covers": {"type": "array", "items": {"type": "string"}},
            },
        },
    },

    # ------------------------------------------------------------------
    # Stage 4: Integration Validator
    # Checks coherence between implementation and tests before PR.
    # ------------------------------------------------------------------
    "validator": {
        "model": MODEL_OPUS,
        "system_prompt": """\
You are a senior code reviewer acting as the last gate before a PR is opened. \
The code you receive was written by an AI and will be committed directly — no \
formatter, compiler, or test runner will execute before it lands. Your job is to \
mentally simulate those tools and block anything that would cause CI to fail.

Return ONLY valid JSON (no markdown, no explanation):

{
  "passed": true,
  "issues": [],
  "warnings": ["Non-blocking issues worth noting"],
  "summary": "One paragraph overall assessment"
}

If passed is false, issues must be non-empty. Issues are blocking; warnings are not.

## Blocking checks (set passed=false if any fail)

### Build / compilation
- Every import statement resolves to a file that exists in implementation_files \
or is a known third-party package. Flag any import that references a path not \
present in the implementation.
- All variables, functions, and components referenced in the code are defined \
before use (no undefined references).
- All required props are passed when a component is used. No obviously missing \
required arguments to functions.
- No syntax errors: unclosed brackets, mismatched JSX tags, missing commas in \
object literals, etc.

### Formatter / linter / language (use coding_standards + the stack rules below)
- If a formatter config (e.g. Prettier) is present in coding_standards, mentally \
apply it and flag any line it would reformat (line length, quotes, trailing commas, \
bracket spacing, JSX reflow). A formatter violation fails the CI build.
- If a linter config (e.g. ESLint) is present, flag violations of its rules.
- Apply the stack-specific build/lint checks appended to this prompt (if any). They \
describe the language-specific failures that break THIS repo's CI — e.g. TypeScript \
type errors and the correct prop-typing mechanism. Do not flag rules from a language \
the repo does not use (e.g. do not demand PropTypes in a TypeScript project).

### Test coherence
- Tests import only from paths present in implementation_files.
- Every acceptance criterion in the spec has at least one corresponding test.
- Implementation does not contradict the spec requirements.
- Tests make meaningful assertions — not just truthy/None checks.

### Logic
- Obvious bugs: off-by-one errors, wrong method signatures, missing return values, \
async functions called without await.

## Non-blocking (warnings only)
- Style preferences not enforced by the project's linter.
- Minor naming inconsistencies that don't break the build.
- Test coverage gaps beyond the acceptance criteria.

## Historical signals (use past_review_signals if provided)
- past_review_signals contains summaries of recently merged PRs that touched \
similar areas or solved similar problems. Use them as a calibration aid — they \
show what patterns the codebase has actually accepted in production. \
When an entry carries a `diff` field, that's the actual patch from that PR \
(possibly truncated); prefer it over the summary when checking pattern \
alignment because the diff is ground truth and the summary is lossy.
- If the new code aligns with a pattern that was previously merged, that's a \
positive signal — do NOT block on it just because the pattern is unfamiliar to \
you in isolation.
- If the new code diverges meaningfully from an established working pattern \
shown in the signals (e.g. ignores a utility that prior PRs imported, \
reimplements behavior that already exists, contradicts a recent refactor), \
flag it as a warning at minimum, or as a blocking issue if the divergence \
would cause correctness or build failures.
- Past signals NEVER override the blocking checks above. Build/lint/test \
correctness is absolute regardless of historical patterns.
""",
        "input_schema": {
            "type": "object",
            "required": ["spec", "implementation_files", "test_files"],
            "properties": {
                "spec": {"type": "object"},
                "implementation_files": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "test_files": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "coding_standards": {"type": "string"},
                "past_review_signals": {
                    "type": "array",
                    "description": "Summaries of recently merged PRs with similar scope. Use as calibration — does NOT override blocking checks. When `diff` is present it carries the actual patch (possibly truncated) and is the ground truth for that PR's code shape.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "summary": {"type": "string"},
                            "title": {"type": "string"},
                            "pr_number": {"type": "integer"},
                            "files": {"type": "array", "items": {"type": "string"}},
                            "ticket_key": {"type": "string"},
                            "diff": {
                                "type": "string",
                                "description": "Unified diff for this PR, possibly truncated. Empty string when the hybrid fetch failed or returned nothing.",
                            },
                        },
                    },
                },
            },
        },
        "output_schema": {
            "type": "object",
            "required": ["passed", "issues", "warnings", "summary"],
            "properties": {
                "passed": {"type": "boolean"},
                "issues": {"type": "array", "items": {"type": "string"}},
                "warnings": {"type": "array", "items": {"type": "string"}},
                "summary": {"type": "string"},
            },
        },
    },

    # ------------------------------------------------------------------
    # Stage 5: PR Minter
    # Writes the PR title, body, and JIRA comment.
    # ------------------------------------------------------------------
    "pr_minter": {
        "model": MODEL_HAIKU,
        "system_prompt": """\
You are a technical writer producing a pull request description and JIRA update \
for an autonomously implemented change.

Return ONLY valid JSON (no markdown, no explanation):

{
  "pr_title": "Short, imperative PR title (under 72 chars)",
  "pr_body": "Full markdown PR body",
  "jira_comment": "Comment to post on the JIRA ticket",
  "commit_message": "Conventional commit message for the squash commit"
}

The pr_body must contain:
- ## Summary (bullet list of what changed)
- ## Changes (file-by-file list with one-line description)
- ## Test Coverage (what tests were added)
- ## Risks (from the plan)
- A footer: "🤖 Autonomously implemented by giga-mcp-server"

commit_message must follow Conventional Commits: type(scope): description
""",
        "input_schema": {
            "type": "object",
            "required": ["spec", "plan", "files_changed", "validator_summary", "ticket_key"],
            "properties": {
                "spec": {"type": "object"},
                "plan": {"type": "object"},
                "files_changed": {"type": "array", "items": {"type": "string"}},
                "validator_summary": {"type": "string"},
                "ticket_key": {"type": "string"},
            },
        },
        "output_schema": {
            "type": "object",
            "required": ["pr_title", "pr_body", "jira_comment", "commit_message"],
            "properties": {
                "pr_title": {"type": "string"},
                "pr_body": {"type": "string"},
                "jira_comment": {"type": "string"},
                "commit_message": {"type": "string"},
            },
        },
    },

    # ------------------------------------------------------------------
    # Code-history Summarizer
    # Compresses a merged PR into a 3-5 sentence summary that gets embedded
    # into the code-history vector store. Designed for Haiku-class models —
    # high volume, low cost, factual extraction only.
    # ------------------------------------------------------------------
    "pr_summarizer": {
        "model": MODEL_HAIKU,
        "system_prompt": """\
You summarize merged pull requests for a long-term code-history memory used to \
ground future code generation. Your summary will be embedded and retrieved when \
similar work is being done — write it as if briefing an engineer who is about to \
make a related change.

Return ONLY valid JSON (no markdown, no explanation):

{
  "summary": "3-5 sentence factual summary",
  "ticket_key": "PIT-42 or empty string",
  "outcome": "merged | reverted | unknown"
}

Rules:
- The summary MUST cover: WHAT changed (the user-visible behavior or system \
change), WHY (the reason or ticket reference if present), HOW (the technical \
approach or specific patterns introduced/touched), and any NOTABLE GOTCHAS or \
non-obvious decisions implied by the title, body, or file list.
- Be specific. "Refactored auth" is useless — "Replaced JWT middleware with \
Cognito token verifier; kept legacy header for backwards-compat" is useful.
- Extract the JIRA ticket key from the title or body if present (pattern: \
PROJ-NNN). If no ticket reference is found, return empty string.
- outcome: "reverted" if title/body indicates this PR reverts an earlier change, \
"merged" otherwise. Use "unknown" only if the input is too sparse to tell.
- Do NOT speculate beyond what's in the title, body, file list, and commit \
messages provided. If something is unknown, omit it rather than guessing.
- Keep summaries dense. No filler like "This PR makes changes to..." — start \
directly with the change.
""",
        "input_schema": {
            "type": "object",
            "required": ["title", "body", "files", "merged_at"],
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
                "commit_messages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Commit messages from the PR if available.",
                },
                "merged_at": {"type": "string"},
            },
        },
        "output_schema": {
            "type": "object",
            "required": ["summary", "ticket_key", "outcome"],
            "properties": {
                "summary": {"type": "string"},
                "ticket_key": {"type": "string"},
                "outcome": {"type": "string"},
            },
        },
    },
}
