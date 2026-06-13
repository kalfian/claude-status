# claude-status

Real-time Claude Code statusline — 5h session %, weekly %, context window, and reset times. Runs as a Stop hook after every response. Zero tokens consumed.

```
Session ────────── 54%  resets today 14:40   Weekly ── 6%  resets Jun 16 17:00   Context ── 78%  156K/200K   Pro · Sonnet 4.6
```

Reads directly from `api.anthropic.com/api/oauth/usage` via OAuth token stored in macOS Keychain. Falls back to JSONL parsing if the API is unavailable.

## Requirements

- macOS (uses Keychain for OAuth token)
- Claude Code with Pro or Max subscription
- Python 3.9+

---

## Install

### Option A — Claude Code plugin (persistent)

Register the marketplace once, then install:

```
/plugin marketplace add kalfian/claude-status
/plugin install claude-status@kalfian-claude-status
```

The Stop hook registers automatically — statusline appears below every response.

On-demand via slash command:

```
/claude-status:status
```

### Option B — Current session only (no marketplace needed)

Download the release asset and point to it directly:

```bash
curl -L https://github.com/kalfian/claude-status/releases/latest/download/claude-status.zip -o /tmp/claude-status.zip
claude --plugin-url /tmp/claude-status.zip
```

Or if you've cloned the repo locally:

```bash
claude --plugin-dir /path/to/claude-status
```

> **Note:** `--plugin-url` does **not** work with GitHub archive URLs (`/archive/main.zip`) because those zips wrap everything inside a `reponame-branch/` subdirectory that Claude Code cannot strip. Use a release asset zip or `--plugin-dir` instead.

### Option C — Manual install (no plugin system)

```bash
git clone https://github.com/kalfian/claude-status.git
cd claude-status
python3 claude_status.py --install
```

Copies the script to `~/.claude/scripts/claude_status.py` and injects a Stop hook into `~/.claude/settings.json`.

---

## Uninstall

### Plugin install (Option A)

```
/plugin uninstall claude-status
```

### Manual install (Option C)

Remove hook only:

```bash
python3 ~/.claude/scripts/claude_status.py --uninstall
```

Full removal — hook + script + config:

```bash
python3 ~/.claude/scripts/claude_status.py --uninstall
rm ~/.claude/scripts/claude_status.py
rm -f ~/.claude/claude-status-config.json
```

---

## Configuration

Optional config at `~/.claude/claude-status-config.json`:

```json
{
  "plan": "pro",
  "no_color": false,
  "quiet_below_pct": 0
}
```

| Key | Default | Description |
|---|---|---|
| `plan` | `"pro"` | `"pro"` or `"max_100"` — used only in JSONL fallback mode |
| `no_color` | `false` | Force plain ASCII output (no ANSI colors) |
| `quiet_below_pct` | `0` | Suppress output when both windows are below this % |

---

## Developer / testing guide

### 1. Clone and run immediately

```bash
git clone https://github.com/kalfian/claude-status.git
cd claude-status
python3 claude_status.py
```

### 2. `--dev` — verbose diagnostics to stderr

Shows data source, API latency, raw values, fallback state, and context info:

```bash
python3 claude_status.py --dev
```

Example output (stderr):

```
claude-status diagnostic  [2026-06-13 10:51:13]
  script      : /path/to/claude_status.py
  python      : 3.14.4  platform=darwin
  plugin_root : (not set — running directly)
  config      : ~/.claude/claude-status-config.json
  plan        : pro
--- primary path: Keychain + API ---
  keychain    : OK  subscription=pro  expires_at=1781347063552
  api fetch   : OK  (142ms)
  raw api     : {"five_hour": {"utilization": 54.0, ...}}
  5h          : pct=54.0%  resets_at=2026-06-13T07:40:00+00:00  est=False
  7d          : pct=6.0%   resets_at=2026-06-16T10:00:00+00:00  est=False
  context     : 78.1% (156K/200K)
  model       : claude-sonnet-4-6
  term_width  : 220  is_fallback=False
  elapsed_ms  : 580
```

### 3. `--debug` — write log to file (use when running as hook)

When the Stop hook fires there's no visible terminal. Use `--debug` to write a log file:

```bash
python3 claude_status.py --debug
```

Log path:
- **macOS / Linux**: `/tmp/claude-status-debug.log`
- **Windows**: `%TEMP%\claude-status-debug.log`

Multiple hook fires append to the same file. Tail it during a session:

```bash
tail -f /tmp/claude-status-debug.log
```

To enable debug mode for the hook, temporarily edit `hooks/hooks.json`:

```json
"command": "python3 \"${CLAUDE_PLUGIN_ROOT}/claude_status.py\" --debug"
```

### 4. Test as plugin locally (before publishing)

```bash
claude --plugin-dir /path/to/claude-status
```

Verify `CLAUDE_PLUGIN_ROOT` is set correctly:

```bash
python3 claude_status.py --dev
# plugin_root : /path/to/claude-status  ← should not say "(not set)"
```

Reload plugin without restarting:

```
/reload-plugins
```

### 5. JSON output

```bash
python3 claude_status.py --mode json | jq .
```

### 6. Run tests

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install pytest
pytest tests/ -v
```

All 56 tests should pass.

---

## Contributing

1. Fork and clone
2. Run tests: `pytest tests/ -v` — all must stay green before opening a PR
3. Test locally with `--dev` and `--debug` before submitting
4. See [RELEASE.md](RELEASE.md) for the release process

---

## How it works

1. Reads OAuth token from macOS Keychain (`Claude Code-credentials`)
2. Calls `api.anthropic.com/api/oauth/usage` — no Cloudflare, no token consumption
3. Reads current context % from the most recently modified JSONL session file
4. Renders a color-coded statusline to stdout via the Stop hook

If the API is unavailable (expired token, network error), falls back to JSONL-based token estimation with an `est.` label.

### Color scheme

| Usage | Color |
|---|---|
| 0–49% | Green |
| 50–74% | Yellow |
| 75–89% | Orange |
| ≥ 90% | Red + ⚠ |

---

## License

MIT
