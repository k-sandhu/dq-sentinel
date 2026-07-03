# Developing on Windows (and inside OneDrive)

Notes for contributors developing DQ Sentinel on Windows, especially when the
repo lives inside a OneDrive-synced folder. None of this applies to CI (Linux)
or to most macOS/Linux setups.

## OneDrive file locks

- **Symptom:** `EPERM`, `WinError 32` (file in use), flaky `npm install` /
  `pip install`. OneDrive holds file handles while syncing.
- **Fix:** retry once; for big installs, pause OneDrive sync or run
  `npm install` again — it is idempotent.

## Keep the Python venv outside OneDrive

Create it at `%USERPROFILE%\.venvs\dq-sentinel` (what `scripts/dev.ps1` does).
Venvs contain running executables that OneDrive loves to lock, plus thousands
of small files that thrash sync.

## node_modules

`node_modules/` is gitignored but still syncs (noise, not breakage). Optionally
mark the repo folder "Always keep on this device" so files-on-demand
placeholders don't break file watchers.

## Symlinks

Symlinks may be unavailable without Windows Developer Mode. `CLAUDE.md` is
committed as a symlink to `AGENTS.md`; if your checkout materializes it as a
plain file containing the path or an `@AGENTS.md` import line, that is expected
— do not "fix" it.

## File watchers

uvicorn `--reload` and Vite occasionally double-fire on OneDrive; harmless.

## SQLite locks

SQLite files under OneDrive can hit transient locks. The app DB uses WAL +
retries; if a test fails with `database is locked`, re-run once before
investigating. Keep worker concurrency at 1 when the app DB is SQLite.

## npm optional-deps bug

`Cannot find module '@rollup/rollup-win32-x64-msvc'` → npm optional-dependencies
bug (worse under OneDrive):

```powershell
npm install @rollup/rollup-win32-x64-msvc --no-save
```

Do **not** add it to `package.json` — it is platform-specific and CI runs Linux.
