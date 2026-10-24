# Git hooks

`lefthook.yml` owns the checks. Install hooks from the repository root with
`lefthook install pre-commit`, using a native Lefthook executable or the prepared
`web/node_modules/lefthook/bin/index.js` through Node. The web lockfiles currently
pin Lefthook 1.13.6; installing hooks does not prepare frontend dependencies.

Installed hooks use `lefthook.sh` to select `.venv/Scripts` on Windows or
`.venv/bin` on Linux/macOS. Prepare that environment with the repository's Python
dependencies, including PyYAML, before committing. Ruff, gofmt and installed
frontend tools are needed when the staged files require their checks. Missing
tools fail the applicable check; hooks never download dependencies.

On Windows use Git for Windows, whose hook environment supplies `sh`. A prepared
native Lefthook can also live at `.local/tools/lefthook.exe`; install with:

```powershell
& .\.local\tools\lefthook.exe install pre-commit
```

The bootstrap invokes native Lefthook or the installed Node entrypoint directly,
preserving message paths and arguments with spaces. It does not use package
manager `.cmd` fallbacks. Direct Linux CI runs keep their `python3` default;
installed Git hooks select the prepared `python` through the bootstrap.

Normal commits retain the existing fix-and-stage behavior. For a read-only
diagnostic, set `LEFTHOOK_CHECK_ONLY=1` and run `git hook run pre-commit`; this
still runs checks but does not fix or stage files. After changing the bootstrap
command, reinstall hooks. Independent LFS hooks are not part of this installer.
