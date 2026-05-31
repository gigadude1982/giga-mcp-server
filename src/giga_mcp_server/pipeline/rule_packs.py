"""Language/framework-specific rule packs for the code-writing agents.

The base prompts in ``agent_prompts.py`` are stack-agnostic. The concrete,
stack-specific rules (PropTypes vs typed props, ``tsc`` constraints, Jest's
``global`` gotcha, etc.) live here and are appended to the implementer /
test_writer / validator system prompts at runtime based on the target repo's
``repo_config`` (``language`` + optional explicit ``stack``).

Why this exists: the agents were hardcoded for JavaScript-React (PropTypes,
no types) and produced code that fails ``tsc`` when pointed at a TypeScript
repo. Splitting the rules by stack lets one pipeline do the right thing for a
JS repo (gigacorp-react) and a TS repo (punch-pwa) without contradiction.

Resolution: an explicit ``stack`` in ``.giga-pipeline.json`` wins; otherwise it
is derived from ``language``. An unknown stack resolves to ``"generic"`` (empty
rules) so the pipeline still runs, leaning entirely on the repo's
``coding_standards`` — that is the agnostic fallback.
"""

from __future__ import annotations

# Map a repo_config.language to a default stack id. Override per-repo with an
# explicit `stack` field when this guess is wrong (e.g. non-React TypeScript).
_LANGUAGE_DEFAULT_STACK: dict[str, str] = {
    "python": "python",
    "py": "python",
    "javascript": "javascript-react",
    "js": "javascript-react",
    "jsx": "javascript-react",
    "typescript": "typescript-react",
    "ts": "typescript-react",
    "tsx": "typescript-react",
}

ROLES = ("implementer", "test_writer", "validator")

# ---------------------------------------------------------------------------
# Rule packs: RULE_PACKS[stack][role] -> str
# ---------------------------------------------------------------------------

RULE_PACKS: dict[str, dict[str, str]] = {
    # -------------------------------------------------------------------
    "python": {
        "implementer": """\
Python rules:
- Follow PEP 8 and the project's existing type-hint conventions; annotate \
public function signatures.
- Imports at the top; no circular imports. Prefer the stdlib and existing \
project dependencies over new ones.
- Reuse existing modules, helpers, and abstractions instead of reimplementing.
- Add concise docstrings to public functions/classes.""",
        "test_writer": """\
pytest rules:
- Name test files test_*.py and place them under the configured test dir.
- Use plain `assert`; use fixtures and monkeypatch for setup and mocking.
- Parametrize edge cases; never perform real network or filesystem I/O — mock it.
- Make concrete assertions on behavior, not just truthiness.""",
        "validator": """\
Python build checks (this code is committed without running anything):
- Every import resolves to the stdlib, a declared dependency, or a file present \
in implementation_files. Flag unresolved imports.
- No undefined names; function signatures match their call sites.
- No syntax errors (indentation, unclosed brackets, bad f-strings).
- Tests make meaningful assertions and cover every acceptance criterion.""",
    },
    # -------------------------------------------------------------------
    "javascript-react": {
        "implementer": """\
JavaScript + React rules:
- Use the React 17+ automatic JSX transform: NEVER add `import React from 'react'` \
just for JSX. Check existing files — if none import React directly, adding it \
trips the no-unused-vars ESLint rule and fails CI.
- Every prop used in a component MUST be declared in a PropTypes definition. A \
missing PropTypes declaration trips the react/prop-types ESLint rule and fails \
the build. Import PropTypes and add a `.propTypes` block at the bottom of the file.
- If you create a CSS module (Foo.module.css) you MUST import it \
(`import styles from './Foo.module.css'`) and reference classes as `styles.x`.
- Prefer existing context hooks over prop drilling; reuse existing layout/wrapper \
components and the state-management patterns already in use.
- If the test plan references data-testid attributes, add them to the matching \
JSX elements.
- If coding_standards includes a Prettier config, your output must already be \
Prettier-formatted. JSX attributes are all-or-nothing: if the opening tag plus \
all attributes fits printWidth on one line, keep them on one line; only break \
when it would exceed printWidth, and then put each attribute on its own line.""",
        "test_writer": """\
React test rules (test_framework is jest or vitest — match the project):
- Use @testing-library/react (render, screen, fireEvent, waitFor) and \
@testing-library/jest-dom matchers. Use jest.fn()/vi.fn() per the framework; \
restore mocks in afterEach.
- Wrap components/hooks that consume a React context in the appropriate Provider.
- Query priority: getByRole > getByLabelText > getByText > getByTestId. Only use \
getByTestId if the implementation actually has that data-testid.
- Pass ALL required props — check the component's PropTypes and supply every \
isRequired prop in every render call.
- Tests live alongside source (src/Foo.test.jsx). No screen.debug(). NEVER add \
`import React` for the automatic JSX transform.""",
        "validator": """\
JavaScript + React build/lint checks:
- Every import resolves to a file in implementation_files or a known package.
- No undefined references; all required props passed; no syntax / mismatched-JSX \
errors.
- ESLint blockers: missing PropTypes (react/prop-types), unused vars including a \
stray `import React` (no-unused-vars), missing useEffect deps \
(react-hooks/exhaustive-deps).
- If a Prettier config is in coding_standards, flag any line it would reformat \
(line length, quotes, trailing commas, JSX attribute reflow).""",
    },
    # -------------------------------------------------------------------
    "typescript-react": {
        "implementer": """\
TypeScript + React rules (generated code MUST pass `tsc -b`):
- Type every prop with a TS interface/type (e.g. `type Props = { ... }`). Do NOT \
use PropTypes — PropTypes is wrong for TypeScript and will not type-check.
- Avoid `any`. Type all props, state, hooks, callbacks, and return values. \
Assume strict mode: no implicit any, no unused locals/parameters \
(noUnusedLocals / noUnusedParameters).
- Use the React 17+ automatic JSX transform: do NOT add `import React from 'react'` \
just for JSX.
- Import any types you reference; every import must resolve to a real module or \
type declaration.
- If you create a CSS module (Foo.module.css) you MUST import it and reference \
classes as `styles.x`.
- Prefer existing context hooks over prop drilling; reuse existing components and \
patterns. Add data-testid attributes the test plan references.
- If coding_standards includes a Prettier config, your output must already be \
Prettier-formatted (same JSX all-or-nothing attribute rule as Prettier enforces).""",
        "test_writer": """\
TypeScript React test rules (Jest + React Testing Library; *.test.tsx beside the \
component; tests MUST pass `tsc`):
- Use `globalThis`, NEVER bare `global` — `global` is not defined in TS DOM/jsdom \
typings and trips `error TS2304: Cannot find name 'global'`. For globals, prefer \
`globalThis.fetch = jest.fn() as jest.Mock` (cast so it type-checks) or import \
from `@jest/globals`.
- Type all mocks, variables, and helper functions — no implicit any. Import any \
types you reference (component Props, fixtures).
- Wrap context consumers in their Provider. Pass every required (non-optional) \
prop — read the component's Props type, not PropTypes.
- Query priority: getByRole > getByLabelText > getByText > getByTestId; only use \
getByTestId if the implementation has that attribute. No screen.debug(). Do NOT \
add `import React` just for JSX.""",
        "validator": """\
TypeScript + React checks (this code must pass `tsc -b` — mentally type-check it):
- Props are typed via an interface/type, NOT PropTypes. Flag any PropTypes usage \
as a TypeScript error.
- No `any` introduced and no implicit any; every variable, prop, mock, and \
function is typed. Flag unused locals/parameters (noUnusedLocals/Parameters).
- Every import resolves to a real module or type; all referenced types are \
imported.
- Test files use `globalThis` (not `global`) and cast mocks so they type-check.
- Plus: no undefined references, all required props passed, no syntax errors. \
Apply ESLint/Prettier from coding_standards.""",
    },
}


def resolve_stack(language: str | None, *, stack: str | None = None) -> str:
    """Resolve the rule-pack stack id for a repo.

    Precedence: an explicit ``stack`` (from .giga-pipeline.json) wins; otherwise
    derive from ``language``. Unknown → ``"generic"`` (no rules; rely on
    coding_standards).
    """
    if stack:
        return stack.strip().lower()
    lang = (language or "").strip().lower()
    return _LANGUAGE_DEFAULT_STACK.get(lang, "generic")


def role_rules(stack: str, role: str) -> str:
    """Return the rule text for a stack+role, or "" when none applies.

    The empty string is the agnostic path: the agent runs on its base prompt
    plus the repo's coding_standards only.
    """
    return RULE_PACKS.get(stack, {}).get(role, "")


def system_suffix(stack: str, role: str) -> str:
    """Rule text wrapped as a labeled system-prompt section, or "" if none."""
    rules = role_rules(stack, role)
    if not rules:
        return ""
    return f"## Stack-specific rules ({stack})\n\n{rules}"
