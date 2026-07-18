# Changelog

All notable changes to this project are documented in this file.

## [0.3.0] - 2026-07-18

### Added

- Adaptive comment collection with valid-comment targets, page budgets, and signal-saturation stopping.
- Semantic signal merging with unique-user, cross-video, duplicate, and variant statistics.
- Default comment privacy redaction with stable anonymous user hashes.
- Claim-level fact status, external-verification gates, novelty scoring, and A/B hook plans.
- Optional historical performance feedback for conservative fit-score calibration.
- Run provenance hashes plus LLM latency, retry, and token-usage metrics.
- Recorded protocol contracts, optional live canary checks, CodeQL, and Dependabot.

### Changed

- Publish-ready status now requires diverse audience evidence and never bypasses high-risk review.
- Duplicate comments remain visible in volume counts but no longer raise semantic confidence.

## [0.2.0] - 2026-07-13

### Added

- Multi-page profile scanning before TopN ranking.
- Bounded concurrent comment collection with retries, per-video status, checkpoints, and failed-only resume.
- Evidence references that link every accepted quote to a video title or comment ID.
- Separate audience-pain and content-hypothesis signal types.
- Deterministic second-pass package audit for grounding, duplication, fabrication, and unsafe CTA patterns.
- Explainable score reasons, publish-ready/exploratory confidence levels, and an offline `evaluate` command.
- GitHub CI lint and package build checks.

### Changed

- Noise-only comments no longer become pain signals.
- Unknown model-generated pain points are rejected.
- Weak-only runs are labeled as exploratory instead of directly shootable.
- LLM retries are limited to transient transport, rate-limit, and server failures.

## [0.1.0]

- Initial public release.
