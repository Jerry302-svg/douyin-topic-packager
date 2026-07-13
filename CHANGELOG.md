# Changelog

All notable changes to this project are documented in this file.

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
