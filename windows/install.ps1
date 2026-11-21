# HSC 2.0 installer for Windows
# Installs for the current user only - no admin needed.
#   install.cmd            -> install
#   uninstall.cmd          -> uninstall
# (or: powershell -ExecutionPolicy Bypass -File install.ps1 [-Uninstall])

param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'

$Dest    = Join-Path $env:LOCALAPPDATA 'Programs\HSC'
$Here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$Classes = 'Software\Classes'
$HKCU    = [Microsoft.Win32.Registry]::CurrentUser

function Get-UserPath {
    $p = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($p) { $p } else { '' }
}

function Remove-FromUserPath($dir) {
    $parts = (Get-UserPath) -split ';' | Where-Object { $_ -and ($_.TrimEnd('\') -ne $dir.TrimEnd('\')) }
    [Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User')
}

function Set-RegDefault($subkey, $value) {
    # .NET registry API: avoids PowerShell treating the '*' key as a wildcard
    $k = $HKCU.CreateSubKey($subkey)
    $k.SetValue('', $value)
    $k.Close()
}

function Remove-RegTree($subkey) {
    try { $HKCU.DeleteSubKeyTree($subkey, $false) } catch { }
}

# ---------------------------------------------------------------- uninstall
if ($Uninstall) {
    Write-Host 'Uninstalling HSC...'
    Remove-FromUserPath $Dest
    Remove-RegTree "$Classes\.hsc"
    Remove-RegTree "$Classes\HSC.File"
    Remove-RegTree "$Classes\*\shell\HSC.Compress"
    # Delete files last: if this was launched from the installed uninstall.cmd,
    # Windows may keep that one file busy - that's fine, everything else goes.
    if (Test-Path $Dest) {
        Get-ChildItem $Dest | Remove-Item -Force -Recurse -ErrorAction SilentlyContinue
        Remove-Item $Dest -Force -ErrorAction SilentlyContinue
    }
    Write-Host 'HSC removed. (Open a new terminal for the PATH change to apply.)'
    exit 0
}

# ---------------------------------------------------------------- find Python
# HSC needs Python 3.8+. The 'py' launcher is preferred because it keeps
# working when Python is upgraded. The Microsoft Store "python" stub fails
# the version check below, so it is skipped automatically.
function Test-Python($exe, [string[]]$pre) {
    try {
        $out = & $exe @pre -c 'import sys; print(sys.version_info >= (3, 8))' 2>$null
        return ($LASTEXITCODE -eq 0 -and "$out".Trim() -eq 'True')
    } catch { return $false }
}

$runner = $null
$py = Get-Command py -ErrorAction SilentlyContinue
if ($py -and (Test-Python $py.Source @('-3'))) {
    $runner = "`"$($py.Source)`" -3"
} else {
    foreach ($name in 'python', 'python3') {
        $c = Get-Command $name -ErrorAction SilentlyContinue
        if ($c -and (Test-Python $c.Source @())) {
            $exe = & $c.Source -c 'import sys; print(sys.executable)'
            $runner = "`"$("$exe".Trim())`""
            break
        }
    }
}
if (-not $runner) {
    Write-Host ''
    Write-Host 'Python 3.8 or newer was not found.' -ForegroundColor Red
    Write-Host 'Install it with:   winget install Python.Python.3.13'
    Write-Host 'or from python.org (tick "Add python.exe to PATH"), then run this again.'
    exit 1
}

# ---------------------------------------------------------------- install files
Write-Host "Installing HSC to $Dest"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
# Release zips have hsc.py next to this script; the source repo has it one level up.
$Src = if (Test-Path (Join-Path $Here 'hsc.py')) { $Here } else { Join-Path $Here '..' }
Copy-Item -Force (Join-Path $Src 'hsc.py') $Dest
Copy-Item -Force (Join-Path $Src 'hsc.magic') $Dest
Copy-Item -Force (Join-Path $Here 'install.ps1') $Dest   # so uninstall works later
@"
@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Uninstall
pause
"@ | Set-Content -Encoding ASCII (Join-Path $Dest 'uninstall.cmd')

# The 'hsc' command itself: a tiny batch file that runs hsc.py with Python.
# %~dp0 = the folder this .cmd lives in, %* = all arguments passed through.
@"
@echo off
$runner "%~dp0hsc.py" %*
"@ | Set-Content -Encoding ASCII (Join-Path $Dest 'hsc.cmd')

# ---------------------------------------------------------------- PATH
$userPath = Get-UserPath
$already = ($userPath -split ';') | Where-Object { $_.TrimEnd('\') -eq $Dest.TrimEnd('\') }
if (-not $already) {
    $newPath = if ($userPath) { "$userPath;$Dest" } else { $Dest }
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
}

# ---------------------------------------------------------------- file type + right-click
$hscCmd = Join-Path $Dest 'hsc.cmd'
Set-RegDefault "$Classes\.hsc" 'HSC.File'
Set-RegDefault "$Classes\HSC.File" 'HSC Compressed File'
Set-RegDefault "$Classes\HSC.File\shell" 'extract'            # double-click = extract
Set-RegDefault "$Classes\HSC.File\shell\extract" 'Extract with HSC'
Set-RegDefault "$Classes\HSC.File\shell\extract\command" "cmd.exe /c `"`"$hscCmd`" d `"%1`" & pause`""
Set-RegDefault "$Classes\*\shell\HSC.Compress" 'Compress with HSC'
Set-RegDefault "$Classes\*\shell\HSC.Compress\command" "cmd.exe /c `"`"$hscCmd`" c `"%1`" & pause`""

Write-Host ''
Write-Host 'HSC installed!' -ForegroundColor Green
Write-Host '  - Open a NEW terminal and run:  hsc --help'
Write-Host '  - Right-click any file -> "Compress with HSC"'
Write-Host '    (Windows 11: it''s under "Show more options")'
Write-Host '  - Double-click (or right-click) a .hsc file -> "Extract with HSC"'
Write-Host "  - To uninstall: run uninstall.cmd (also copied to $Dest)"
