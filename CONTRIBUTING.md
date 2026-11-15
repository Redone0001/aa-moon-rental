# Contributing

Create a branch from `master`, keep changes focused and open a pull request. Never push directly to `master` or create a parallel `main` branch. One approving review and all required checks must pass before merge; your branch must be current with `master`. Self-approval does not count. Maintainers must follow the same process. A sole maintainer will need a second collaborator to review their own future changes.

Run the development checks listed in the README. Include a migration for model changes. Explain the user-visible change, the reason and relevant validation in the PR. Add meaningful regression tests for bidding, money, permissions, billing dates and retention. Update documentation when behavior changes. Include screenshots for visible UI changes in both light and dark themes.

Use the service layer for all writes. Preserve UTC billing rules, `Decimal` money arithmetic, database locking order, immutable invoice snapshots and payment deduplication. Never introduce raw wallet history, token values or private character data into logs or fixtures. New permissions must be checked on the server, including service calls, rather than only hiding controls in templates.

CI uses read-only permissions for untrusted PRs, pinned Actions and separate security scanning. Do not use `pull_request_target` to execute contributor code, or add secrets to a test workflow. Dependency changes should document their compatibility with Alliance Auth and aa-moonmining.

Report security issues privately through GitHub Security Advisories as described in SECURITY.md. Be constructive and respectful in issues and reviews.
