<#
The visual probe in the test VM (#155), run from this PC:

    powershell -File tools\ui_probe_vm.ps1 [-Variant light-100,dark-100] [-Surface settings,usage] [-Out folder]

Takes a free test VM from vmtest's pool if this branch has none yet. Copies
this checkout into the VM, installs Python and the app's packages
there the first time (following vmtest's own recipe), runs
tools\ui_probe_guest.ps1 for each variant in tools\ui_probe_plan.json (all of
them by default), and copies the pictures and descriptions back to -Out (a
new folder under %TEMP%\tcp-ui-probe by default). Nothing opens on this PC:
the windows, the theme changes and the scaling all happen in the VM, through
vmtest (TheWorkBench\vmtest).
#>
param(
    [string[]]$Variant = @(),
    [string[]]$Surface = @(),
    [string]$Out = '',
    [string]$VmTest = $(if ($env:VMTEST_HOME) { $env:VMTEST_HOME } else { 'C:\Users\kelly\GitHub\TheWorkBench\vmtest' })
)
$ErrorActionPreference = 'Stop'
# From cmd or powershell -File, "a,b" arrives as one string.
$Variant = @($Variant | ForEach-Object { $_ -split ',' } | Where-Object { $_ })
$Surface = @($Surface | ForEach-Object { $_ -split ',' } | Where-Object { $_ })
$repo = Split-Path $PSScriptRoot
Import-Module (Join-Path $VmTest 'VmTest.psm1') -Force
Push-Location $repo
try {
    # vmtest keeps a pool of VMs (TheWorkBench #208): this task's is the one
    # its lock names, not necessarily the first. A branch that has none yet
    # takes a free one, rather than stopping to be told to.
    $vmName = Find-TaskVM (Resolve-Task)
    if (-not $vmName) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $VmTest 'vmtest.ps1') begin
        if ($LASTEXITCODE) { throw "vmtest couldn't give this branch a test VM (exit $LASTEXITCODE)." }
        $vmName = Find-TaskVM (Resolve-Task)
    }
} finally { Pop-Location }
if (-not $vmName) { throw "This task holds no test VM, and 'vmtest begin' didn't give it one." }

$plan = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'ui_probe_plan.json') -Raw | ConvertFrom-Json
$variants = @($plan.windows | Where-Object { -not $Variant -or $Variant -contains $_.tag })
if (-not $variants) { throw "No Windows variant called $($Variant -join ', ') in ui_probe_plan.json." }
if (-not $Out) { $Out = Join-Path $env:TEMP ("tcp-ui-probe\" + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
New-Item $Out -ItemType Directory -Force | Out-Null

# The VM's Python, by its full path: PATH in vmtest's runs doesn't catch up with an install.
$guestPython = 'C:\Users\vmuser\AppData\Local\Programs\Python\Python312\python.exe'
$venvPython = 'C:\vmtest\tcp-venv\Scripts\python.exe'
# No spaces and no double quotes: Windows PowerShell drops inner quotes on the way to vmtest.
$readyCheck = "$venvPython -c __import__('wx');__import__('markdown');__import__('theideaplace_appkit')"

function Invoke-Vm([string]$Command, [int]$Timeout = 600, [switch]$Elevated) {
    # vmtest's output goes to the screen; only the exit code comes back.
    $vmArgs = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                '-File', (Join-Path $VmTest 'vmtest.ps1'), 'run', $Command, '-Timeout', $Timeout)
    if ($Elevated) { $vmArgs += '-Elevated' }
    Push-Location $repo
    try {
        & powershell @vmArgs | Out-Host
        return $LASTEXITCODE
    } finally { Pop-Location }
}

$config = Get-VmTestConfig
$credential = New-Object System.Management.Automation.PSCredential($config.UserName,
    (ConvertTo-SecureString $config.Password -AsPlainText -Force))
$session = New-PSSession -VMName $vmName -Credential $credential
$failed = @()
try {
    # This checkout as it stands (tracked and new files, not ignored ones).
    $zip = Join-Path $env:TEMP 'tcp-probe-source.zip'
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip }
    $files = git -C $repo ls-files -co --exclude-standard |
        Where-Object { Test-Path -LiteralPath (Join-Path $repo $_) -PathType Leaf }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::Open($zip, 'Create')
    try {
        foreach ($file in $files) {
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, (Join-Path $repo $file), $file) | Out-Null
        }
    } finally { $archive.Dispose() }
    Invoke-Command -Session $session -ScriptBlock {
        # A probe left over from a run that was stopped holds vmtest's output
        # file, and every later vmtest run would be refused.
        $left = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'ui_probe(_guest)?\.(py|ps1)' })
        foreach ($p in $left) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
        # Gone, not just told to go: one still running keeps its pictures locked.
        foreach ($p in $left) { Wait-Process -Id $p.ProcessId -Timeout 15 -ErrorAction SilentlyContinue }
        Remove-Item C:\vmtest\tcp -Recurse -Force -ErrorAction SilentlyContinue
        # An old run's pictures left here would be copied back as this run's.
        if (Test-Path C:\vmtest\probe-out) { Remove-Item C:\vmtest\probe-out -Recurse -Force -ErrorAction Stop }
        New-Item C:\vmtest -ItemType Directory -Force | Out-Null
    }
    Copy-Item -LiteralPath $zip -Destination C:\vmtest\tcp-source.zip -ToSession $session -Force
    Invoke-Command -Session $session -ScriptBlock { Expand-Archive C:\vmtest\tcp-source.zip C:\vmtest\tcp -Force }

    # Ready means wx imports, not just that the venv is there: a half-finished install isn't.
    if ((Invoke-Vm $readyCheck) -ne 0) {
        "Installing Python and the app's packages in the VM (first run only)..."
        if ((Invoke-Vm "$guestPython --version") -ne 0) {
            Invoke-Vm 'winget install --id Python.Python.3.12 -e --architecture x64 --scope user --silent --accept-source-agreements --accept-package-agreements' 900 | Out-Null
        }
        # wxPython needs the Visual C++ runtime, which a clean Windows lacks ("DLL load failed").
        Invoke-Vm 'winget install --id Microsoft.VCRedist.2015+.x64 -e --silent --accept-source-agreements --accept-package-agreements' 900 -Elevated | Out-Null
        if ((Invoke-Vm "$guestPython -m venv C:\vmtest\tcp-venv") -ne 0) { throw "Couldn't make the VM's venv." }
        if ((Invoke-Vm "$venvPython -m pip install -q -r C:\vmtest\tcp\requirements-dev.txt" 2400) -ne 0) {
            throw "Couldn't install the app's packages in the VM."
        }
        if ((Invoke-Vm $readyCheck) -ne 0) { throw "wxPython still doesn't import in the VM." }
    }

    foreach ($v in $variants) {
        "== $($v.tag)"
        # Theme names are single words (night-sky, not "night sky"): quotes don't survive vmtest run.
        $hc = if ($v.hc_theme) { $v.hc_theme } else { 'aquatic' }
        $command = "powershell -NoProfile -ExecutionPolicy Bypass -File C:\vmtest\tcp\tools\ui_probe_guest.ps1 -Tag $($v.tag) -Theme $($v.theme) -HcTheme $hc -Scale $($v.scale)"
        $names = if ($Surface) { $Surface } elseif ($v.surfaces) { $v.surfaces } else { @() }
        if ($names) { $command += " -Surface " + ($names -join ',') }
        if ((Invoke-Vm $command 1800) -ne 0) { $failed += $v.tag }
    }
    if (Invoke-Command -Session $session -ScriptBlock { Get-ChildItem C:\vmtest\probe-out -File -ErrorAction SilentlyContinue }) {
        Copy-Item C:\vmtest\probe-out\* -Destination $Out -FromSession $session -Recurse -Force
    }
} finally {
    Remove-PSSession $session
}
"Pictures and descriptions: $Out"
if ($failed) { "Variants with failures: $($failed -join ', ')"; exit 1 }
