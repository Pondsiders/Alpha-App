# Changelog

All notable changes to Alpha-App land here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
semver where practical. One entry per user-visible change. Internal refactors
and plumbing go in Git.

## [Unreleased]

### Added

- GitHub Milestones for release planning: `v1.0.0 (fork-ready)`, `v1.x`, `Someday`.
- GitHub Labels for cross-cutting concerns: `fork-blocker`, `epic`, `frontend`, `backend`, `infra`.
- Branch protection on `main` (PR required; admin bypass enabled).
- PR template at `.github/pull_request_template.md` (Summary / Why / Test plan).
- `delete_branch_on_merge` enabled at the repo level — merged PR branches auto-delete.
- **War Plan Rosemary**: 30 new issues filed and the existing 15 reconciled into milestones. Pinned roadmap tracker at #117.
  - 4 epics (Streaming redesign #87, Dead-code audit #88, MCP functional decomposition #89, Tool components as a system #90)
  - 18 sub-issues linked via GitHub's native sub-issue API
  - 12 standalone issues across v1.0.0, v1.x, and Someday
- `CHANGELOG.md` (this file).

### Changed

- **Planning for v1.0.0 is live.** See the [v1.0.0 (fork-ready) milestone](https://github.com/Pondsiders/Alpha-App/milestone/1) and the [pinned roadmap](https://github.com/Pondsiders/Alpha-App/issues/117).

[Unreleased]: https://github.com/Pondsiders/Alpha-App/commits/main
