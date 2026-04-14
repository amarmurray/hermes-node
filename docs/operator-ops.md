# hermes-node operator ops

Quick reference for operating the hermes-node wrapper on the VPS.

## Pull / update the repo

```bash
cd ~/apps/hermes-node
git fetch origin
git log --oneline HEAD..origin/main   # review incoming changes
git pull --ff-only origin main
```

If `--ff-only` fails, the local branch has diverged. Investigate before force-resetting.

## Restart the service

```bash
sudo systemctl restart hermes-node
sudo systemctl status hermes-node
```

To stop it without restart:

```bash
sudo systemctl stop hermes-node
```

## Inspect logs

systemd journal (live tail):

```bash
journalctl -u hermes-node -f
```

JSONL wrapper log:

```bash
tail -50 ~/apps/hermes-node/logs/hermes-node.jsonl | python3 -m json.tool --no-ensure-ascii
```

Filter for rate-limit events:

```bash
grep 'deferred_rate_limit' ~/apps/hermes-node/logs/hermes-node.jsonl | tail -20
```

## Check queue status

```bash
~/apps/hermes-node/bin/hermes-node status
~/apps/hermes-node/bin/hermes-node status --json          # machine-readable
~/apps/hermes-node/bin/hermes-node status --task-id <ID>  # single task detail
```

## Back up the SQLite DB

```bash
cp ~/apps/hermes-node/state/hermes-node.db ~/apps/hermes-node/state/hermes-node.db.bak.$(date +%Y%m%d_%H%M%S)
```

For a consistent backup while the service is running (WAL mode):

```bash
sqlite3 ~/apps/hermes-node/state/hermes-node.db ".backup '/tmp/hermes-node-backup.db'"
```

OVH automated backups also cover the full VPS disk.

## Recover from a bad deploy

1. Stop the service: `sudo systemctl stop hermes-node`
2. Check `git log` for the last known-good commit
3. Reset: `git checkout <good-commit>`
4. Restore DB backup if schema migration was destructive
5. Restart: `sudo systemctl start hermes-node`

## GitHub deploy key

Recommended for this host. A read-only deploy key avoids storing personal credentials on the VPS.

```bash
ssh-keygen -t ed25519 -C "hermes-node-deploy@vps" -f ~/.ssh/hermes_node_deploy -N ""
cat ~/.ssh/hermes_node_deploy.pub
# Add to GitHub repo > Settings > Deploy keys (read-only)
```

Then configure the repo to use it:

```bash
cd ~/apps/hermes-node
git remote set-url origin git@github.com:amarmurray/hermes-node.git
```

Add to `~/.ssh/config`:

```
Host github.com
  IdentityFile ~/.ssh/hermes_node_deploy
  IdentitiesOnly yes
```

## Environment variables (retry/backoff)

These can be set in `~/apps/hermes-node/.env` or as environment overrides:

| Variable | Default | Description |
|---|---|---|
| `HERMES_NODE_MAX_ATTEMPTS` | 5 | Max retries before permanent failure |
| `HERMES_NODE_BASE_BACKOFF_SECONDS` | 30 | Initial backoff delay |
| `HERMES_NODE_MAX_BACKOFF_SECONDS` | 900 | Maximum backoff cap (15 min) |
| `HERMES_NODE_POLL_INTERVAL_SECONDS` | 15 | Queue poll interval |
