# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Project skeleton: `src/incidently` package, `pyproject.toml`, pytest and ruff config.
- GitHub Actions CI running lint and tests on every supported Python version (3.11 through 3.15).
- Roadmap in `docs/roadmap.md`.

### Changed
- CI uses `actions/checkout@v7` and `actions/setup-python@v7`, which run on Node.js 24 instead of the deprecated Node.js 20.
