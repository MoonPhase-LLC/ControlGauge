# S1-09: build the Windows installer with the bundled service.
# See docs/PACKAGING.md. Run from anywhere:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/package-windows.ps1
#   ... -ServiceOnly   # steps 1-2 only (used by CI)
#
# Steps:
#   1. Freeze the service with PyInstaller (--onedir) from an isolated build
#      environment holding only the locked runtime dependencies + PyInstaller.
#   2. Check the frozen bundle for build/dev-only packages.
#   3. Build the release app and NSIS installer with the bundle as a resource.
#
# Every native command's exit code is checked immediately: PowerShell does
# not stop on a failing native command by itself.

param([switch]$ServiceOnly)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Invoke-Native {
    param([string]$Description, [scriptblock]$Command)
    Write-Host "==> $Description"
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Description failed (exit code $LASTEXITCODE)" }
}

$repo = Split-Path -Parent $PSScriptRoot
$service = Join-Path $repo 'service'
$app = Join-Path $repo 'app'
$buildVenv = Join-Path $service 'build\package-venv'
$workPath = Join-Path $service 'build\pyinstaller'
$distPath = Join-Path $service 'dist\pyinstaller'
$bundle = Join-Path $distPath 'controlgauge-service'

# 1. Freeze the service. A dedicated environment (not service/.venv) so the
#    bundle cannot pick up dev tools such as mypy or pytest.
foreach ($old in $workPath, $distPath) {
    if (Test-Path $old) { Remove-Item $old -Recurse -Force }  # throws if in use
}
$env:UV_PROJECT_ENVIRONMENT = $buildVenv
try {
    Push-Location $service
    Invoke-Native 'Create the isolated build environment (frozen, no dev group)' {
        # --reinstall-package: uv caches the built wheel of this local project
        # keyed on pyproject.toml, so source-only changes would otherwise be
        # frozen from a stale wheel on a developer machine.
        uv sync --frozen --no-dev --group package --no-editable --reinstall-package controlgauge-service
    }
} finally {
    Pop-Location
    Remove-Item Env:UV_PROJECT_ENVIRONMENT
}
$python = Join-Path $buildVenv 'Scripts\python.exe'
Invoke-Native 'Show the bundled Python version' { & $python --version }

# Modules PyInstaller would otherwise collect that the service never uses at
# runtime: pydantic's optional mypy plugin, and PyInstaller's own setuptools
# dependency.
$excludes = @('mypy', 'pydantic.mypy', 'setuptools', 'pkg_resources', '_distutils_hack', 'pip')
$pyiArgs = @(
    '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--console', '--noupx',
    '--name', 'controlgauge-service',
    '--distpath', $distPath, '--workpath', $workPath, '--specpath', $workPath
)
foreach ($m in $excludes) { $pyiArgs += @('--exclude-module', $m) }
# Sprint 2: Alembic loads migration scripts (env.py, versions/*.py) from files
# at runtime, not from the PYZ archive, so they ship as data next to the
# package (`db/migrate.py` resolves them relative to itself).
$migrations = Join-Path $service 'src\controlgauge_service\migrations'
$pyiArgs += @('--add-data', "$migrations;controlgauge_service\migrations")
# Frameworks (Sprint 3): each directory ships whole -- framework.json, the
# pinned catalog/profile sources it declares, and the upstream license.
# `framework_catalog.py` reads `<bundle>/_internal/frameworks/`, and the
# service imports the catalog on first start after verifying its SHA-256.
foreach ($meta in Get-ChildItem (Join-Path $repo 'frameworks') -Recurse -Filter 'framework.json') {
    $pyiArgs += @('--add-data', "$($meta.Directory.FullName);frameworks\$($meta.Directory.Name)")
}
$pyiArgs += (Join-Path $service 'packaging\pyinstaller_entry.py')
Invoke-Native 'Freeze the service (PyInstaller --onedir)' { & $python @pyiArgs }

# 2. Inspect the bundle.
$exe = Join-Path $bundle 'controlgauge-service.exe'
if (-not (Test-Path $exe -PathType Leaf)) { throw "frozen service not found at $exe" }
$forbidden = @('mypy', 'setuptools', 'pip', 'pytest', '_pytest', 'httpx', 'ruff', 'PyInstaller', 'tests')
$found = Get-ChildItem (Join-Path $bundle '_internal') -Directory |
    Where-Object { $forbidden -contains $_.Name -or $_.Name -like 'pytest*' -or $_.Name -like 'mypy*' }
if ($found) { throw "bundle contains build/dev-only packages: $($found.Name -join ', ')" }
# Pure-Python modules live in the PYZ archive inside the executable.
$archive = & $python -m PyInstaller.utils.cliutils.archive_viewer --list --recursive --brief $exe
if ($LASTEXITCODE -ne 0) { throw "could not list the frozen archive (exit code $LASTEXITCODE)" }
$pattern = '^\s*(' + (($forbidden | ForEach-Object { [regex]::Escape($_) }) -join '|') + ')(\.|$)'
$bad = @($archive | Where-Object { $_ -match $pattern })
if ($bad.Count -gt 0) { throw "frozen archive contains build/dev-only modules: $(($bad | Select-Object -First 10) -join ', ')" }
foreach ($required in 'controlgauge_service.supervised', 'controlgauge_service.evidence.worker', 'controlgauge_service.evidence.parsers', 'pypdf') {
    # Sprint 4: the same executable is the contained parser worker
    # (`--parse-worker`), so the worker, the parsers and pypdf must be frozen in.
    if (-not ($archive | Where-Object { $_ -match ('^\s*' + [regex]::Escape($required) + '$') })) {
        throw "frozen archive is missing $required"
    }
}
$bundledMigrations = Join-Path $bundle '_internal\controlgauge_service\migrations'
if (-not (Test-Path (Join-Path $bundledMigrations 'env.py') -PathType Leaf) -or
    -not (Get-ChildItem (Join-Path $bundledMigrations 'versions') -Filter '*.py')) {
    throw 'bundle is missing the database migrations'
}
$bundledManifests = @(Get-ChildItem (Join-Path $bundle '_internal\frameworks') -Recurse -Filter 'framework.json' -ErrorAction SilentlyContinue)
if ($bundledManifests.Count -eq 0) { throw 'bundle is missing framework metadata' }
# Every pinned source a manifest declares must be in the bundle, byte-identical:
# the service refuses a catalog whose hash does not match, so a bad copy here
# would ship an app with no catalog.
foreach ($manifestFile in $bundledManifests) {
    $manifest = Get-Content -Raw -Encoding UTF8 $manifestFile.FullName | ConvertFrom-Json
    $sources = @()
    if ($manifest.catalog) { $sources += $manifest.catalog }
    if ($manifest.profiles) { $sources += @($manifest.profiles) }
    foreach ($source in $sources) {
        $file = Join-Path $manifestFile.Directory.FullName ($source.path -replace '/', '\')
        if (-not (Test-Path $file -PathType Leaf)) { throw "bundle is missing framework source $($source.path)" }
        $actual = (Get-FileHash -Algorithm SHA256 $file).Hash.ToLowerInvariant()
        if ($actual -ne $source.sha256) { throw "bundled framework source $($source.path) does not match its pinned SHA-256" }
    }
}
Write-Host "Frozen service OK: $exe"
if ($ServiceOnly) { return }

# 3. Release app + NSIS installer. tauri.package.conf.json adds the bundle as
#    a resource (<install dir>/service/) and selects the NSIS target.
try {
    Push-Location $app
    Invoke-Native 'Install frontend dependencies (frozen)' { npm ci }
    Invoke-Native 'Build the release app and NSIS installer' {
        npm run tauri build -- --config src-tauri/tauri.package.conf.json
    }
} finally {
    Pop-Location
}

$installers = @(Get-ChildItem (Join-Path $app 'src-tauri\target\release\bundle\nsis') -Filter '*-setup.exe')
if ($installers.Count -ne 1) { throw "expected exactly one NSIS installer, found $($installers.Count)" }
$installer = $installers[0]
$hash = (Get-FileHash $installer.FullName -Algorithm SHA256).Hash
Write-Host ''
Write-Host "Installer: $($installer.FullName)"
Write-Host "Size:      $($installer.Length) bytes"
Write-Host "SHA-256:   $hash"
