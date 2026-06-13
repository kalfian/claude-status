# claude-status

Real-time Claude Code statusline — 5h session %, weekly %, context window, and reset times. Runs as a Stop hook. Zero tokens consumed.

```
Session ────────── 54%  resets today 14:40   Weekly ────── 6%  resets Jun 16 17:00   Context ── 78%  156K/200K   Pro · Sonnet 4.6
```

Reads directly from `api.anthropic.com/api/oauth/usage` via OAuth token stored in macOS Keychain. Falls back to JSONL parsing if the API is unavailable.

## Requirements

- macOS (uses Keychain for OAuth token)
- Claude Code with Pro or Max subscription
- Python 3.9+

---

## Install via Claude Code plugin

```
/plugin install claude-status@kalfian/claude-status
```

The plugin registers a Stop hook automatically — the statusline appears below every response.

On-demand via slash command:

```
/claude-status:status
```

---

## Manual install (without plugin system)

```bash
git clone https://github.com/kalfian/claude-status.git
cd claude-status
python3 claude_status.py --install
```

This copies the script to `~/.claude/scripts/claude_status.py` and injects a Stop hook into `~/.claude/settings.json`.

To remove:

```bash
python3 claude_status.py --uninstall
```

---

## Developer / testing guide

### 1. Clone and test immediately (no install needed)

```bash
git clone https://github.com/kalfian/claude-status.git
cd claude-status
python3 claude_status.py
```

### 2. Developer mode — verbose diagnostics to stderr

Shows data source, API latency, raw values, fallback state, and detected model:

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
  raw api     : {"five_hour": {"utilization": 54.0, ...}, ...}
  5h          : pct=54.0%  resets_at=2026-06-13T07:40:00+00:00  est=False
  7d          : pct=6.0%   resets_at=2026-06-16T10:00:00+00:00  est=False
  context     : 78.1% (156K/200K)
  model       : claude-sonnet-4-6
  term_width  : 220  is_fallback=False
  elapsed_ms  : 580
```

### 3. Debug mode — write log to file (use when running as hook)

When the Stop hook fires there's no visible terminal. Use `--debug` to write a log file instead:

```bash
python3 claude_status.py --debug
```

Log is written to:
- **macOS / Linux**: `/tmp/claude-status-debug.log`
- **Windows**: `%TEMP%\claude-status-debug.log`

Multiple hook fires append to the same file. Tail it during a session:

```bash
tail -f /tmp/claude-status-debug.log
```

To use debug mode via the hook, temporarily edit `hooks/hooks.json`:

```json
"command": "python3 \"${CLAUDE_PLUGIN_ROOT}/claude_status.py\" --debug"
```

### 4. Test as Claude Code plugin (local, before publish)

```bash
# Launch Claude Code pointing at local plugin directory
claude --plugin-dir /path/to/claude-status
```

The Stop hook fires from the local directory. Verify `CLAUDE_PLUGIN_ROOT` is set correctly:

```bash
python3 claude_status.py --dev
# plugin_root : /path/to/claude-status  ← should not say "(not set)"
```

### 5. JSON output (machine-readable)

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

### 7. Reload plugin mid-session

```
/reload-plugins
```

---

## Uninstall

### Plugin install (via `/plugin install`)

Remove only the plugin, keep nothing:

```
/plugin uninstall claude-status
```

### Manual install (via `--install`)

Remove the Stop hook from `settings.json`, keep the script:

```bash
python3 ~/.claude/scripts/claude_status.py --uninstall
```

Full removal — remove hook AND script file:

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
| `no_color` | `false` | Force plain ASCII output |
| `quiet_below_pct` | `0` | Suppress output when both windows are below this % |

---

## Contributing

1. Fork and clone the repo
2. Run tests: `pytest tests/ -v` — all must stay green
3. Test locally with `--dev` before opening a PR
4. PRs that break tests will not be merged

---

## How it works

1. Reads OAuth token from macOS Keychain (`Claude Code-credentials`)
2. Calls `api.anthropic.com/api/oauth/usage` (no Cloudflare, no token consumption)
3. Reads current context % from the most recently modified JSONL session file
4. Renders a color-coded statusline, printed to stdout by the Stop hook

If the API is unavailable (expired token, network error), it falls back to JSONL-based estimation with an `est.` label.
