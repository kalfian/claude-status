# Release Guide

## Versioning

Follows [Semantic Versioning](https://semver.org): `vMAJOR.MINOR.PATCH`

| Change type | Version bump |
|---|---|
| Bug fix, dependency update | PATCH (`v0.1.0` → `v0.1.1`) |
| New feature, backward-compatible | MINOR (`v0.1.0` → `v0.2.0`) |
| Breaking change (hook interface, config schema) | MAJOR (`v0.1.0` → `v1.0.0`) |

Pre-release suffix: `-alpha`, `-beta`, `-rc.1` (e.g. `v0.1.0-beta`).

---

## How to release

### 1. Update version in plugin manifest

Edit `.claude-plugin/plugin.json`:

```json
{
  "version": "0.2.0"
}
```

### 2. Commit and push

```bash
git add .claude-plugin/plugin.json
git commit -m "chore: bump version to v0.2.0"
git push
```

### 3. Create and push a tag

```bash
git tag v0.2.0
git push origin v0.2.0
```

This triggers the GitHub Actions release workflow automatically.

### 4. Verify

The workflow will:
1. Run all 56 tests
2. Create a GitHub Release with auto-generated changelog
3. Attach `claude_status.py` as a release asset

---

## GitHub Actions workflows

| File | Trigger | Purpose |
|---|---|---|
| `.github/workflows/ci.yml` | push / PR to `main` | Run tests on Python 3.9/3.11/3.13 × macOS/Linux/Windows |
| `.github/workflows/release.yml` | push `v*` tag | Run tests, build zip, create GitHub Release |

---

## Changelog format

Each release entry should cover:
- What changed (features, fixes, removals)
- Migration notes if any config/hook interface changed
- Minimum Python version if changed

See [GitHub Releases](https://github.com/kalfian/claude-status/releases) for history.
