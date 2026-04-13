# Upstream Hermes Notes

These notes capture the upstream runtime contract this wrapper preserves.

## Repository

- upstream repo: [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)
- local inspection commit before sync: `2773b18b`
- latest upstream GitHub release observed: `v2026.4.13`
- release publication time: `2026-04-13T18:52:41Z`
- deployed VPS tag: `v2026.4.13`
- deployed VPS commit: `1af2e18d408a9dcc2c61d6fc1eef5c6667f8e254`
- package version reported by `hermes version`: `0.9.0`
- inspection date: April 13, 2026

The VPS is now pinned to a tagged upstream release rather than an untagged `main` snapshot.

## Documented install path

Primary upstream references:

- [installation](https://hermes-agent.nousresearch.com/docs/getting-started/installation/)
- [quickstart](https://hermes-agent.nousresearch.com/docs/getting-started/quickstart/)
- [CLI reference](https://hermes-agent.nousresearch.com/docs/reference/cli-commands/)
- [API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/)

The upstream docs define:

- code checkout in the cloned upstream repo
- runtime state in `~/.hermes/`
- secrets in `~/.hermes/.env`
- config in `~/.hermes/config.yaml`
- CLI symlink at `~/.local/bin/hermes`

## CLI workflow

Official verification commands:

```bash
hermes version
hermes doctor
hermes status
hermes chat -q "Hello! What tools do you have available?"
```

Useful upstream commands for this wrapper:

```bash
hermes gateway install
hermes gateway start
hermes gateway stop
hermes gateway restart
hermes gateway status
hermes chat -q "..."
```

## Local-only API server

The upstream API server is documented as:

- `API_SERVER_ENABLED=true`
- `API_SERVER_KEY=...`
- default bind `127.0.0.1`
- default port `8642`

Temporal should eventually talk to this local-only API rather than importing upstream internals through the wrapper repo.
