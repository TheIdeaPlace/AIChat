<#
What tools\ui_probe_vm.ps1 needs to know of the requirements files to keep
the VM's packages in step with this checkout (#585). Dot-sourced by that
script, and by tests\test_ui_probe_vm.py, which checks it without a VM.

A saved VM checkpoint keeps the packages it had. pip install -r leaves a
package alone when its version number hasn't changed, and app-kit is pinned
to a commit archive whose version number stays the same from commit to
commit: #581 moved the pin to add a sound, the VM kept the old copy, and
File, Sounds failed there. So the probe stamps the venv with a hash of the
requirements files after installing, and when the hash differs it installs
again, reinstalling by force each package pinned to an archive (a direct
URL) rather than to a version number.

Works in Windows PowerShell 5.1, which the probe runs in.
#>

# The files the VM's venv is installed from (requirements-dev.txt takes in
# requirements.txt with -r).
$ProbeRequirementFiles = @('requirements.txt', 'requirements-dev.txt')

function Get-ProbeRequirementsStamp([string]$Repo) {
    <# A hash of the requirements files, the same whatever line endings the
    checkout has. A missing file counts as empty, so the stamp still
    changes when one appears. #>
    $text = foreach ($name in $ProbeRequirementFiles) {
        $path = Join-Path $Repo $name
        $body = if (Test-Path -LiteralPath $path -PathType Leaf) {
            [System.IO.File]::ReadAllText($path) -replace "`r`n", "`n"
        } else { '' }
        "== $name`n$body"
    }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes(($text -join "`n"))
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha.ComputeHash($bytes)) -replace '-', '').ToLowerInvariant()
    } finally { $sha.Dispose() }
}

function Get-ProbeDirectUrlRequirements([string]$Repo) {
    <# The URL of each requirement pinned to an archive ("name @ URL"), from
    the requirements files, in order and once each: the ones a version check
    can't tell apart. Comments, options (-r, --hash) and environment
    markers are left out. #>
    $urls = New-Object System.Collections.Generic.List[string]
    foreach ($name in $ProbeRequirementFiles) {
        $path = Join-Path $Repo $name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        foreach ($line in [System.IO.File]::ReadAllLines($path)) {
            $line = ($line -replace '(^|\s)#.*$', '').Trim()
            if ($line -match '^[A-Za-z0-9][A-Za-z0-9._-]*(\[[^\]]*\])?\s*@\s*(\S+)') {
                $url = $Matches[2] -replace ';.*$', ''
                if ($url -and -not $urls.Contains($url)) { $urls.Add($url) }
            }
        }
    }
    return $urls.ToArray()  # one by one, as PowerShell does: @() around it for a list
}

function Test-ProbeVenvCurrent([string]$Stored, [string]$Current) {
    <# Whether the stamp kept in the VM's venv matches this checkout's. No
    stamp (a venv made before #585) isn't current. #>
    return [bool]$Stored -and ($Stored.Trim() -eq $Current)
}
