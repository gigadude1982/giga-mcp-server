from __future__ import annotations

from typing import Any

AGENT_REGISTRY: dict[str, dict[str, Any]] = {
    # ------------------------------------------------------------------
    # Stage 1: Ticket Digester
    # Normalises a raw JIRA ticket into a structured implementation spec.
    # ------------------------------------------------------------------
    "digester": {
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
""",
        "input_schema": {
            "type": "object",
            "required": ["ticket_key", "summary", "description", "issue_type", "priority", "labels"],
            "properties": {
                "ticket_key": {"type": "string"},
                "summary": {"type": "string"},
                "description": {"type": "string"},
                "issue_type": {"type": "string"},
                "priority": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
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
- Follow the coding standards exactly.
- Do not add unrelated changes outside the scope of the plan.
- If action is "delete", return content as empty string.
- Imports must be at the top. No circular imports.
- Study related_files carefully before writing anything — reuse existing components, \
hooks, utilities, context providers, and module patterns rather than reimplementing them.
- If a shared component or utility already exists that satisfies the need, import and \
use it. Do not create duplicates.
- Match the naming conventions, file structure, and export style of existing files.
- For React projects: prefer existing context hooks over prop drilling, reuse existing \
layout/wrapper components, and follow the same state management patterns already in use.
- For React projects: every prop used in a component MUST be declared in a PropTypes \
definition. Missing PropTypes will trigger the react/prop-types ESLint rule and fail \
the build. Always import PropTypes and add a .propTypes block at the bottom of the file.
- If you create a CSS module file (e.g. Foo.module.css), you MUST import it in the \
component: `import styles from './Foo.module.css'` and reference classes as \
`styles.className`. Never create a CSS module and leave it unimported.
- If the test plan references data-testid attributes, you MUST add those attributes \
to the corresponding JSX elements in the implementation (e.g. data-testid="footer-tagline").
- If validator_feedback is present, your PRIMARY job is to fix every issue listed \
before anything else. Each item is a blocking problem from a previous attempt that \
caused the build, tests, or linter to fail. Address every single one explicitly — \
do not skip any, and do not introduce new issues while fixing them.
- CRITICAL: Your output will be committed directly without running a formatter. If \
coding_standards includes a Prettier config, your code MUST already be formatted exactly \
as Prettier would format it — correct line length, quote style, trailing commas, bracket \
spacing, and JSX formatting. A Prettier lint error will fail the CI build. When in doubt, \
break long lines and match the style of existing_content exactly.
- JSX attribute formatting: Prettier's rule is ALL-or-nothing per element. If the opening \
tag including ALL attributes fits within printWidth on ONE line, every attribute MUST stay \
on that same line — do NOT break short tags across multiple lines. Only break attributes \
to separate lines when the entire opening tag would exceed printWidth. Count the full \
line: indentation + tag name + space + all attributes + closing `>`. If it fits, keep it \
on one line. If it doesn't fit, put each attribute on its own indented line with `>` on \
its own line. Never put some attributes on one line and others on new lines.
- React imports: NEVER add `import React from 'react'` in files that use the automatic \
JSX transform (React 17+). Check existing_content and related_files — if no other file \
imports React directly, the project uses the automatic transform and the import is \
unnecessary. Adding it will trigger the no-unused-vars ESLint rule and fail the build.
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
- The file path extension must match the project language (e.g. .test.js for JavaScript, \
.test.py for Python).

React / Jest specific rules (apply when test_framework is jest):
- Use @testing-library/react: render, screen, fireEvent, waitFor, userEvent.
- Use @testing-library/jest-dom matchers (toBeInTheDocument, toHaveTextContent, etc).
- Always wrap components or hooks that consume a React context in the appropriate \
Provider — failure to do so will cause tests to throw at runtime.
- Mock fetch/axios calls with jest.fn() or jest.spyOn(); restore mocks in afterEach.
- Prefer queries in this order: getByRole, getByLabelText, getByText, getByTestId.
- Only use getByTestId if the implementation file contains a matching data-testid attribute. \
Never reference a data-testid that doesn't exist in the component.
- Always pass ALL required props when rendering a component. Check the component's \
PropTypes definition and pass every isRequired prop in every render/renderHook call. \
Missing required props will cause tests to fail or render undefined values.
- Do not use screen.debug() in committed tests.
- Test files live alongside source (e.g. src/components/Foo.test.js, not tests/Foo.test.js).
- NEVER add `import React from 'react'` in test files for projects using the automatic \
JSX transform (React 17+). Check implementation_contents — if no file imports React \
directly, the project uses the automatic transform and the import is an unused variable \
that will fail the no-unused-vars ESLint rule.
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

### Formatter / linter (use coding_standards if provided)
- If a Prettier config is present in coding_standards, mentally apply it. Flag \
any line that would be reformatted: lines exceeding printWidth, wrong quote style, \
missing/extra trailing commas, incorrect bracket spacing, or JSX that Prettier \
would reflow. A Prettier violation will fail the CI build.
- JSX attribute formatting (critical): if an opening tag + ALL its attributes fits \
within printWidth on one line, Prettier REQUIRES them on one line. Flag any element \
where attributes are unnecessarily broken across multiple lines when they would fit \
on one line. This is a common Prettier violation — check every JSX element.
- If an ESLint config is present, flag violations of its rules. Common blockers: \
missing PropTypes definitions (react/prop-types), unused variables (no-unused-vars), \
missing useEffect dependency arrays (react-hooks/exhaustive-deps).

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
}
