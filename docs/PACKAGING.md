# Packaging (zipping a local checkout)

Exclude these before zipping — all are local/generated, not source:

- `.venv/`, `venv/` — virtualenv
- `__pycache__/`, `*.pyc` — bytecode cache
- `*.egg-info/`, `build/`, `dist/` — install/build artifacts
- `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/` — tool caches
- `.coverage`, `htmlcov/` — coverage output
- `*.db` (except your own data you intend to ship) — scan result databases
- `.git/` — if present, unless you want history included

Two options depending on what the zip is for:

## Option A — Source-only zip (sharing / releasing to others)

No git history included. Already covered by `.gitignore` for git-based
workflows; for a manual zip:

```bash
zip -r redirecthunter.zip . \
  -x "*.venv/*" -x "*__pycache__/*" -x "*.egg-info/*" \
  -x "*.mypy_cache/*" -x "*.ruff_cache/*" -x "*.pytest_cache/*" \
  -x "*.coverage" -x "*htmlcov/*" -x "*.db" -x "*link.csv" \
  -x "links.csv" -x "redirects.csv" -x "results.csv" -x "*.zip" -x "*.git/*"
```

## Option B — Personal backup with git history (`.git` included)

For your own backups, it's useful to keep some commit history without
shipping the *entire* history (which can bloat the archive on a
long-lived repo). `archive-redirecthunter.sh` (place it at the repo
root) automates this:

1. Checks that `git`, `zip`, `du`, and `mktemp` are available.
2. Confirms you're inside the repo and finds its root.
3. Removes any existing output zip first (idempotent — safe to re-run).
4. Creates a **shallow clone** (`git clone --depth N --no-local`) that
   truncates history to the last N commits (default `10`) — the
   original repo is untouched.
5. Zips the shallow clone (`.git` included) with the same excludes as
   Option A.
6. Verifies the zip was created, reports its size and commit count, and
   writes a timestamped log to `.archive-logs/`.

```bash
chmod +x archive-redirecthunter.sh

./archive-redirecthunter.sh                     # last 10 commits -> ./redirecthunter.zip
./archive-redirecthunter.sh -n 5                # keep only the last 5 commits
./archive-redirecthunter.sh -o ~/backups/rh.zip # custom output path
./archive-redirecthunter.sh -k                  # keep the temporary shallow clone (debugging)
./archive-redirecthunter.sh -h                  # show all options
```

Note: a shallow-cloned `.git` shows as a "shallow repository" when
inspected later (`git log` stops at the graft point) — the full history
still lives safely in your original, un-touched repo.

Back to [main README](../README.md).
