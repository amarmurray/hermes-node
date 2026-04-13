# Upstream Hermes Notes

These notes capture the upstream runtime contract this wrapper preserves.

## Repository

- upstream repo: [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)
- local inspection commit before sync: `2773b18b`
- latest upstream GitHub release observed: `v2026.4.13`
- release publication time: `2026-04-13T18:52:41Z`
- deployed VPS tag: `v2026.4.13`
- deployed VPS commit: `1af2e18d408a9dcc2c61d6fc1eef5c6667f8e254`
- deployed VPS checkout: `/home/ubuntu/apps/hermes-agent`
- package version reported by `hermes version`: `0.9.0`
- inspection date: April 13, 2026

The VPS is now pinned to a tagged upstream release rather than an untagged `main` snapshot.

## Documented install path

Primary upstream references:

- [installation](https://hermes-agent.nousresearch.com/docs/getting-started/installation/)
- [quickstart](https://hermes-agent.nousresearch.com/docs/getting-started/quickstart/)
- [CLI reference](https://hermes-agent.nousresearch.com/docs/reference/cli-commands/)
- [API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/)
- [OpenRouter quickstart](https://openrouter.ai/docs/quickstart)

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

Current VPS proof sequence:

```bash
hermes version
hermes doctor
hermes status
hermes chat --provider openrouter -m 'openai/gpt-oss-20b:free' -Q --max-turns 2 -q "Reply with exactly: ALIVE"
```

Observed working proof output:

```text
ALIVE
```

Current minimal upstream credential on the VPS:

- `OPENROUTER_API_KEY` in `~/.hermes/.env`
- `~/.hermes/.env` permissions tightened to `600`

The OpenRouter credential is wired in the standard upstream location and uses the OpenRouter OpenAI-compatible base URL already present in `~/.hermes/config.yaml`:

```text
https://openrouter.ai/api/v1
```

Notes from proofing:

- `hermes chat --provider openrouter -m openai/gpt-5.2 ...` reached OpenRouter and failed on current credit limits.
- Some free OpenRouter models do not advertise tool use, which matters because Hermes may invoke `clarify` during a chat turn.
- `openai/gpt-oss-20b:free` worked cleanly for the bounded proof command above without changing upstream defaults.

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

Current VPS state intentionally keeps the gateway and API server stopped.

Temporal should eventually prefer one of two ingress shapes:

- wrapper CLI ingress first
- upstream local-only API ingress on `127.0.0.1` later

For this phase, wrapper CLI ingress remains the recommended next step because it preserves the narrowest upstream-first posture.
