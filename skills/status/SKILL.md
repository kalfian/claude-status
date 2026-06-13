---
name: status
description: Show current Claude Code usage — 5h session %, weekly %, context window, and reset times. Use when the user asks "show status", "check usage", "claude status", or wants to uninstall/remove this plugin.
---

Run the following command and display its output to the user exactly as-is (it contains ANSI color codes):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/claude_status.py"
```

Do not add any commentary. Just show the output.

## Uninstall

If the user wants to fully remove this plugin and clean up settings.json, run the deactivate command first (while the plugin is still installed), then tell the user to uninstall:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/claude_status.py" --plugin-deactivate
```

After that, the user runs: `/plugins uninstall claude-status@kalfian-claude-code`
