# Contributing Guidelines

## Development Process
1. **Branching:** Use feature branches (e.g., `feature/req-1.1-inference-engine`). 
2. **Pull Requests:** All PRs must reference a requirement or property ID from the design document.
3. **Commits:** Use conventional commit messages (`feat:`, `fix:`, `docs:`, `test:`).

## Code Style
- **Formatting:** PEP 8 enforced via `Black`.
- **Type Hints:** Required for all function signatures and complex variables. Enforced via `mypy`.
- **Documentation:** Google-style docstrings are mandatory for all public classes and functions.

## Testing Requirements
- **Unit Tests:** New features must include unit tests.
- **Property Tests:** Anything mapped to a design document "Property" MUST include a hypothesis-based property test in `/tests/property/`. This is non-negotiable for proving correctness to reviewers.
- **CI/CD:** All PRs run security scans (bandit, pip-audit) which must pass.

## Code Review Expectations
Reviewers must explicitly check for:
- Hardcoded secrets or credentials.
- Proper asynchronous error handling and retries.
- Clear labeling of TEE/zkML and inter-team mocks.

## Issue Reporting
Use the templates in `.github/ISSUE_TEMPLATE/` for:
- Bugs and features.
- Integration blockers (use the specific "blocked on Team A/C" template for cross-team visibility).
