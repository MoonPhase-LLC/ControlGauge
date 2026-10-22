# Packaging (Windows)

Status: S1-09 packaged-build spike, complete. The clean Windows test passed (see S1-09 results).
This is a minimal proof that the Python service can ship inside the Tauri app and run on a machine
without a development toolchain. It is not release
packaging: there is no code signing, auto-update, installer customization or non-Windows support
(Sprint 16). The outcome is recorded in `DECISIONS.md` D-001.

## What gets built

| Item | Value |
|---|---|
| Package format | NSIS installer (`ControlGauge_<version>_x64-setup.exe`), Tauri's default per-user install mode: no administrator rights, installs to `%LOCALAPPDATA%\ControlGauge` |
| Installed layout | `app.exe` (Tauri host), `uninstall.exe`, `service\controlgauge-service.exe` + `service\_internal\` (frozen Python service) |
| Size (S1-09 build) | Installer 13.0 MB; installed 83 files, 32.7 MB (service 28.1 MB) |
| Signing | Unsigned. Windows SmartScreen may warn on first run |
| Prerequisite | Microsoft Edge WebView2 Runtime. Preinstalled on Windows 11 and current Windows 10. The installer uses Tauri's default `downloadBootstrapper` mode, so it would try to download WebView2 only if it is missing. No other runtime is needed |

Versions in the S1-09 build: Python 3.12.7 (CPython, from the build machine's interpreter),
PyInstaller 6.22.3 with pyinstaller-hooks-contrib 2026.8, FastAPI 0.115.14, Starlette 0.46.2,
uvicorn 0.34.3, pydantic 2.13.5, pydantic-settings 2.15.0, Tauri 2.11.5, tauri-cli 2.11.4,
NSIS 3.11 (downloaded by the Tauri bundler). Python package versions come from `service/uv.lock`.

## Bundling decision: PyInstaller `--onedir`

| Option | Outcome |
|---|---|
| **PyInstaller `--onedir`** (chosen) | Established tool with maintained hooks for FastAPI, pydantic and uvicorn. The bootloader runs Python *in its own process*, so the service is **one process**, the same process the supervisor spawns, assigns to its Job Object and talks to over stdio. Files are installed once, nothing is extracted at run time |
| PyInstaller `--onefile` | Rejected: the bootloader starts a second process and extracts the runtime to `%TEMP%` on every launch. That adds a process to supervise (the real interpreter would be a grandchild) and writes executable code to a user-writable temp directory |
| python.org embeddable distribution + site-packages | Workable, but needs hand-built `._pth` paths, a pip-install step into a target folder and our own dependency-pruning, all maintained in-house. No process-model advantage over `--onedir` |
| Nuitka (compile to C) | Rejected for the spike: needs a C compiler in the build and much longer builds, for no benefit this ticket needs |
| Tauri `externalBin` sidecar | Not used: it needs `tauri-plugin-shell` and capability entries. The S1-04 resolver deliberately loads the service as a plain bundle resource instead (`supervisor/process.rs`) |

New dependency: `pyinstaller>=6.11,<7` in a separate `package` dependency group in
`service/pyproject.toml` (with its own dependencies `pyinstaller-hooks-contrib`, `altgraph`,
`pefile`, `pywin32-ctypes`, `setuptools`). It is a build tool only and never ships: the default
`uv sync` (development, CI) does not install the group, and the build script freezes from an
isolated environment, then rejects the bundle if any build or dev package is inside it.

## How the installed app finds and runs the service

- Release builds resolve the service only at `<resource dir>\service\controlgauge-service.exe`
  (`supervisor/process.rs`, `resolve_production`). On Windows, Tauri's resource directory is the
  directory holding the installed `app.exe`. It is never PATH, the repository, a virtual
  environment, or the current working directory. The development resolver (`service/.venv`) is
  compiled out of release builds, and a missing executable fails closed.
- The frozen bundle reaches that location through `app/src-tauri/tauri.package.conf.json`, which
  only the packaging build merges (`tauri build --config ...`). Debug, CI and `cargo test` builds
  don't reference it.
- The command line is `controlgauge-service.exe --supervised`. The startup secret and session
  token travel only over the private stdio channel (D-025). Startup, the HMAC endpoint challenge,
  the retained authenticated connection (D-026), transport revocation and Job Object cleanup are
  the same code as in development. `packaging/pyinstaller_entry.py` calls the same dispatcher as
  `python -m controlgauge_service`.
- Process model: one service process per app run. That is one fewer than development, where the
  venv's `python.exe` launcher starts the real interpreter as a second process. The service is in
  the app's kill-on-close Job Object. Windows also attaches a console host (`conhost.exe`) to the
  service. That happens for any console-subsystem program, even one started with
  `CREATE_NO_WINDOW`, and it happens in development too. `conhost.exe` is a child of the service,
  so it is inside the same Job Object. It was observed to exit with the service on normal close.
- Trust: the per-user install directory is writable by the user. Anything that can replace
  `service\controlgauge-service.exe` there can equally replace `app.exe`, so resolving from the
  install directory adds no new exposure. A per-machine install under `Program Files`, which only
  administrators can modify, is a Sprint 16 decision.

## Migrating from EvidenceGraph

ControlGauge was previously named EvidenceGraph.

**Existing S1-09 test installs.** The ControlGauge installer does not upgrade an EvidenceGraph
install. The Tauri NSIS installer derives the install directory
(`%LOCALAPPDATA%\<productName>`) and the uninstall registry entry from the product name, and the
app identifier changed from `com.evidencegraph.app` to `com.controlgauge.app`. The ControlGauge
build therefore installs alongside the old one, in `%LOCALAPPDATA%\ControlGauge`. Uninstall
EvidenceGraph from Windows Settings → Apps to avoid two copies. Its WebView2 profile,
`%LOCALAPPDATA%\com.evidencegraph.app`, may be left behind and can be deleted. It holds no
evidence, because the app does not handle evidence yet.

**Developer environments.** The service's environment prefix changed from `EVIDENCEGRAPH_` to
`CONTROLGAUGE_` (`service/src/controlgauge_service/config.py`). Rename these variables in shells
and scripts:

| Old | New |
|---|---|
| `EVIDENCEGRAPH_HOST` | `CONTROLGAUGE_HOST` |
| `EVIDENCEGRAPH_PORT` | `CONTROLGAUGE_PORT` |
| `EVIDENCEGRAPH_ENVIRONMENT` | `CONTROLGAUGE_ENVIRONMENT` |
| `EVIDENCEGRAPH_SESSION_TOKEN` | `CONTROLGAUGE_SESSION_TOKEN` |

The old names are no longer read. A leftover `EVIDENCEGRAPH_SESSION_TOKEN` therefore leaves the
standalone service with no token, and it rejects every request (fail closed). The Python package
(`controlgauge_service`) and console script (`controlgauge-service`) were renamed too, so run
`uv sync` in `service/` once after updating.

**Kept on purpose.** The startup-challenge HMAC domain separator stays
`EvidenceGraph-S1-04-startup-challenge-v1` (`challenge.py`, `challenge.rs`). It is a versioned
security-protocol constant that users never see, not branding. Changing it changes every
challenge response, so it belongs in a deliberate protocol version change, not a rename.

## Build

On Windows, with the development prerequisites from `CONTRIBUTING.md` (Rust via rustup, Node,
uv, and a CPython 3.12 that uv can find or download), run from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/package-windows.ps1
```

The script:
1. creates `service/build/package-venv` with `uv sync --frozen --no-dev --group package --no-editable`;
2. freezes the service with PyInstaller `--onedir` into `service/dist/pyinstaller/controlgauge-service/`, excluding `mypy`, `pydantic.mypy`, `setuptools`, `pkg_resources`, `_distutils_hack` and `pip`;
3. rejects the bundle if `_internal` or the embedded module archive contains `mypy`, `setuptools`, `pip`, `pytest`, `httpx`, `ruff`, `PyInstaller` or `tests`, or lacks `controlgauge_service.supervised`;
4. runs `npm ci`, then `npm run tauri build -- --config src-tauri/tauri.package.conf.json`;
5. prints the installer path, size and SHA-256.

Output: `app/src-tauri/target/release/bundle/nsis/ControlGauge_0.1.0_x64-setup.exe`. All build
output is under git-ignored `build/`, `dist/` and `target/` directories, so no binaries are
committed.

`-ServiceOnly` stops after step 3. CI uses it in the **Packaged service (Windows, PyInstaller)**
job and then runs the supervised end-to-end suite against the frozen executable:

```powershell
cd service
uv run --frozen pytest tests/test_supervised_process.py --service-exe dist/pyinstaller/controlgauge-service/controlgauge-service.exe
```

## Clean-machine validation

`scripts/validate-installed-windows.ps1` checks an installed package using only Windows
PowerShell 5.1. It reads the app's UI through Windows UI Automation (the accessibility tree that
WebView2 exposes), so the shipped build needs no debugging port. It records:
- the OS, the WebView2 runtime version and whether the session is elevated;
- whether Python, Node, npm, Rust or uv is on PATH (each must be absent);
- whether any network adapter is up (the network must be disconnected);
- the installed file hashes.

It then checks:
1. launch from an unrelated working directory, "Service ready", and the authenticated health result shown in the UI;
2. exactly one service process, launched from the install directory, with no secret on its command line and a loopback-only listener;
3. no non-loopback connection from `app.exe` or the service (connections made by the WebView2 runtime itself are reported separately);
4. after the service is killed, the UI shows "Service unavailable", the stale health result is cleared, and the app closes normally;
5. normal close leaves no service process and no listener on its port;
6. forced termination of `app.exe` takes the service with it.

Steps: copy the installer and script to the machine and disconnect the network. Run the
installer, then:
`powershell -NoProfile -ExecutionPolicy Bypass -File validate-installed-windows.ps1`.
The exit code is 0 only when every check passes. A report file is written next to the script.

## Logs and diagnostics

The release build writes **no persistent logs**:
- `app.exe` is a GUI-subsystem program with no console. Its diagnostic logging is compiled out of
  release builds.
- The service writes JSON log lines to stderr. `app.exe` reads them and, in release builds,
  discards them.
- The only on-disk app data is WebView2's profile, `%LOCALAPPDATA%\com.controlgauge.app\EBWebView`.
- The only failure detail exposed is the fixed failure-reason code that the UI shows as
  "Technical detail: …".

`scripts/diagnose-installed-windows.ps1` collects what can be observed from outside without
changing the app. It needs only Windows PowerShell 5.1 and makes no network use. The report is
timestamped and saved as `s1-09-diagnostics-<time>.txt` next to the script, or to `-ReportPath`.
It records:
- the environment: OS, WebView2 version, elevation, network adapters, and the *names* of any
  `CONTROLGAUGE_*`/`WEBVIEW2_*` variables;
- installed file sizes and hashes, and whether the service files are missing (for example
  quarantined);
- a timeline of the app's process tree, with paths and command lines for non-WebView2 processes;
- listening ports and UI states read through UI Automation;
- normal close, exit codes, leftover processes and listeners;
- the PE subsystem of both executables, Application-log crash/hang events, Microsoft Defender
  detections, and new Windows Error Reporting folders.

It records no environment variable values and does not intercept the private startup channel.
Long token-like strings in UI text and command lines are redacted. It cannot capture anything
that happened before it started, such as installation or SmartScreen prompts, or what the window
looked like. Those need a screenshot.

## S1-09 results

These results predate the rename to ControlGauge. The tested build used the EvidenceGraph
identity: installer `EvidenceGraph_0.1.0_x64-setup.exe`, install directory
`%LOCALAPPDATA%\EvidenceGraph`, app identifier `com.evidencegraph.app`, service executable
`service\evidencegraph-service.exe`. The names and hashes below are as originally recorded for
that build.

Build under test: commit `3ab1b4a`, installer SHA-256
`748B9E658F273D954CCF331ADEFD6AF98575963B26AFE43310380ED493155AF2` (12,996,163 bytes).
Installed `app.exe` SHA-256 `923BD1B949ADFD6424B66B70FB0597576706910D4A2714B450D957613FD928E8`;
`service\evidencegraph-service.exe` SHA-256
`C1C5F9F444E52355E1AD13777717DFEAFFCB8DCBD9EA879EA4A7516B2D8A2ED8`.

**Development machine (not clean-machine evidence).** This was Windows 11 Home 10.0.26200,
WebView2 154.0.4258.62, not elevated. All functional checks (1–6 above) passed. The six
environment checks failed as expected, because the toolchain is installed and the network is up,
so the validator correctly refused to treat this machine as clean.

**Clean machine (manual test by the project owner).** Passed. The owner installed the package on
a clean Windows machine and reported:
- the installer completed very quickly;
- it created a Start Menu shortcut;
- the app worked well.

Not recorded for this run: the exact Windows edition and build, the install duration, the hash of
the installer that was used, and a `validate-installed-windows.ps1` report. If a later run needs
check-by-check evidence, run the validator and attach its report.

**Automated CI (separate from the manual test).** The **Packaged service (Windows, PyInstaller)**
job freezes the service with `scripts/package-windows.ps1 -ServiceOnly` and runs the supervised
end-to-end suite against the frozen executable on `windows-latest`. CI does not build the NSIS
installer and does not install anything; the installed-package check is the manual test above.

### Findings

- **WebView2 runtime network traffic.** With a network available, the WebView2 browser process
  (not `app.exe` and not the service) opened HTTPS connections to Microsoft addresses
  (40.99.x.x, 40.104.x.x, 52.96.x.x port 443) within seconds of launch. This is the runtime's own
  configuration/telemetry traffic. The app's CSP (`connect-src ipc: http://ipc.localhost`) stops
  the frontend from making such requests, and nothing the app handles is sent. It is still
  outbound traffic from a local-first product. Follow-up: decide whether to restrict it, for
  example with WebView2 browser arguments or policies, and record that in `SECURITY.md`.
- **Build paths in `app.exe`.** The release binary contains 206 Cargo-registry source paths from
  dependency panic locations, which include the build account's user name. They are not secrets.
  Follow-up for release builds: `--remap-path-prefix` (Sprint 16).
- **Unsigned installer.** SmartScreen may warn. Code signing is Sprint 16.
- **Bundled interpreter provenance.** The build freezes whichever CPython 3.12 uv selects on the
  build machine (here, the python.org 3.12.7 build). A release build should pin the interpreter,
  for example a uv-managed Python.
