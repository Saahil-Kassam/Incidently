# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Project skeleton: `src/incidently` package, `pyproject.toml`, pytest and ruff config.
- GitHub Actions CI running lint and tests on every supported Python version (3.11 through 3.15).
- Roadmap in `docs/roadmap.md`.
- Scenario schema (`incidently.scenario`): strict Pydantic models that reject unknown keys and
  check hosts, grading criteria, and `{{placeholder}}` declarations when a file loads.
- Scenario catalog (`incidently.catalog`): loads `<id>.yaml` files with clear per-file errors,
  and `instantiate()` produces a seeded, reproducible playthrough with a hidden root cause.
- Four networking scenarios: DHCP pool exhaustion, Wi-Fi 802.1X/RADIUS outage,
  DNS cache poisoning, and an OSPF/EIGRP routing loop.
- Runtime dependencies: `pydantic` and `ruamel.yaml`.

### Changed
- CI uses `actions/checkout@v7` and `actions/setup-python@v7`, which run on Node.js 24 instead of the deprecated Node.js 20.
