<#
Runs inside the test VM (#155), started by tools\ui_probe_vm.ps1: sets one
variant (light or dark apps, High Contrast, display scaling), runs the probe,
then puts the display back to light, no High Contrast, 100%.

It changes Windows-wide settings, which is why it only runs in the VM: it
refuses on a machine that isn't a Hyper-V guest.
#>
param(
    [Parameter(Mandatory)][string]$Tag,
    [ValidateSet('light', 'dark', 'high-contrast')][string]$Theme = 'light',
    [string]$HcTheme = 'aquatic',
    [int]$Scale = 100,
    [string]$Out = 'C:\vmtest\probe-out',
    [string]$Python = 'C:\vmtest\tcp-venv\Scripts\python.exe',
    [string]$Source = 'C:\vmtest\tcp',
    [string[]]$Surface = @()
)
$ErrorActionPreference = 'Stop'
# From cmd, -Surface a,b arrives as one string.
$Surface = @($Surface | ForEach-Object { $_ -split ',' } | Where-Object { $_ })

$model = (Get-CimInstance Win32_ComputerSystem).Model
if ($model -ne 'Virtual Machine') {
    throw "ui_probe_guest.ps1 changes Windows' theme and scaling, so it only runs in the test VM (this is a '$model')."
}

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class ProbeDisplay {
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint msg, UIntPtr wParam, string lParam,
                                            uint flags, uint timeout, out UIntPtr result);

    // Tells running programs a setting changed (dark mode is "ImmersiveColorSet").
    public static void Broadcast(string what) {
        UIntPtr result;
        SendMessageTimeout((IntPtr)0xffff, 0x001A, UIntPtr.Zero, what, 2, 5000, out result);
    }

    // The scheme name is a pointer Windows owns: as a string, .NET would
    // free it after the call and corrupt the heap.
    [StructLayout(LayoutKind.Sequential)]
    struct HIGHCONTRAST { public uint cbSize; public uint dwFlags; public IntPtr lpszDefaultScheme; }

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool SystemParametersInfo(uint action, uint param, ref HIGHCONTRAST hc, uint winIni);

    public static bool HighContrastOn() {
        var hc = new HIGHCONTRAST();
        hc.cbSize = (uint)Marshal.SizeOf(typeof(HIGHCONTRAST));
        SystemParametersInfo(0x0042, hc.cbSize, ref hc, 0);
        return (hc.dwFlags & 1) != 0;
    }

    [DllImport("user32.dll", EntryPoint = "SystemParametersInfoW", SetLastError = true)]
    static extern bool SystemParametersInfoPtr(uint action, uint param, IntPtr value, uint winIni);

    // "Underline access keys" (Settings, Accessibility, Keyboard): always,
    // so every run draws them the same. Left to Windows, a window shows them
    // or not by whether the keyboard was used last, which differs between
    // runs and VMs and showed as changes on every dialog (#155).
    public static void SetKeyboardCues(bool on) {
        const uint SPI_SETKEYBOARDCUES = 0x100B, SPIF_SENDCHANGE = 2;
        SystemParametersInfoPtr(SPI_SETKEYBOARDCUES, 0, (IntPtr)(on ? 1 : 0), SPIF_SENDCHANGE);
    }

    // ClearType on, for the same reason: on a fresh VM the first run's
    // WebView drew its text in grey and every later one in ClearType, and
    // the User Guide showed as 7% changed (#155).
    public static void SetClearType() {
        const uint SPI_SETFONTSMOOTHING = 0x004B, SPI_SETFONTSMOOTHINGTYPE = 0x200B;
        const uint FE_FONTSMOOTHINGCLEARTYPE = 2, SPIF_UPDATEINIFILE = 1, SPIF_SENDCHANGE = 2;
        SystemParametersInfoPtr(SPI_SETFONTSMOOTHING, 1, IntPtr.Zero, SPIF_UPDATEINIFILE | SPIF_SENDCHANGE);
        SystemParametersInfoPtr(SPI_SETFONTSMOOTHINGTYPE, 0, (IntPtr)FE_FONTSMOOTHINGCLEARTYPE,
                                SPIF_UPDATEINIFILE | SPIF_SENDCHANGE);
    }

    // ---- display scaling, live, through DisplayConfig (what Settings does) ----

    [StructLayout(LayoutKind.Sequential)] struct LUID { public uint Low; public int High; }

    [StructLayout(LayoutKind.Sequential)]
    struct PATH_SOURCE { public LUID adapterId; public uint id; public uint modeInfoIdx; public uint statusFlags; }

    [StructLayout(LayoutKind.Sequential)]
    struct PATH_TARGET {
        public LUID adapterId; public uint id; public uint modeInfoIdx; public uint outputTechnology;
        public uint rotation; public uint scaling; public uint refreshNum; public uint refreshDen;
        public uint scanLineOrdering; public bool targetAvailable; public uint statusFlags;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct PATH_INFO { public PATH_SOURCE source; public PATH_TARGET target; public uint flags; }

    [StructLayout(LayoutKind.Sequential)]
    struct MODE_INFO {
        public uint infoType; public uint id; public LUID adapterId;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 48)] public byte[] mode;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct HEADER { public int type; public uint size; public LUID adapterId; public uint id; }

    [StructLayout(LayoutKind.Sequential)]
    struct DPI_GET { public HEADER header; public int minRel; public int curRel; public int maxRel; }

    [StructLayout(LayoutKind.Sequential)]
    struct DPI_SET { public HEADER header; public int scaleRel; }

    [DllImport("user32.dll")] static extern int GetDisplayConfigBufferSizes(uint flags, out uint paths, out uint modes);
    [DllImport("user32.dll")] static extern int QueryDisplayConfig(uint flags, ref uint paths, [Out] PATH_INFO[] p,
                                                                   ref uint modes, [Out] MODE_INFO[] m, IntPtr topology);
    [DllImport("user32.dll")] static extern int DisplayConfigGetDeviceInfo(ref DPI_GET packet);
    [DllImport("user32.dll")] static extern int DisplayConfigSetDeviceInfo(ref DPI_SET packet);

    // ---- screen resolution: the VM starts at 1024x768, too small for the
    // main window at more than 100%, so the run uses the largest mode ----

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct DEVMODE {
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmDeviceName;
        public short dmSpecVersion, dmDriverVersion, dmSize, dmDriverExtra;
        public int dmFields;
        public int dmPositionX, dmPositionY, dmDisplayOrientation, dmDisplayFixedOutput;
        public short dmColor, dmDuplex, dmYResolution, dmTTOption, dmCollate;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmFormName;
        public short dmLogPixels;
        public int dmBitsPerPel, dmPelsWidth, dmPelsHeight, dmDisplayFlags, dmDisplayFrequency;
        public int dmICMMethod, dmICMIntent, dmMediaType, dmDitherType, dmReserved1, dmReserved2,
                   dmPanningWidth, dmPanningHeight;
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern bool EnumDisplaySettings(string device, int mode, ref DEVMODE dm);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern int ChangeDisplaySettings(ref DEVMODE dm, int flags);
    [DllImport("user32.dll")]
    static extern int ChangeDisplaySettings(IntPtr dm, int flags);

    static DEVMODE NewMode() {
        var dm = new DEVMODE();
        dm.dmSize = (short)Marshal.SizeOf(typeof(DEVMODE));
        return dm;
    }

    public static string Resolution() {
        var dm = NewMode();
        EnumDisplaySettings(null, -1, ref dm);
        return dm.dmPelsWidth + "x" + dm.dmPelsHeight;
    }

    // The largest mode the display offers, for this session only.
    public static string UseLargestResolution() {
        DEVMODE best = NewMode(), dm = NewMode();
        for (int i = 0; EnumDisplaySettings(null, i, ref dm); i++) {
            if ((long)dm.dmPelsWidth * dm.dmPelsHeight > (long)best.dmPelsWidth * best.dmPelsHeight) best = dm;
            dm = NewMode();
        }
        if (best.dmPelsWidth == 0) throw new Exception("the display lists no modes");
        best.dmFields = 0x80000 | 0x100000;  // DM_PELSWIDTH | DM_PELSHEIGHT
        int result = ChangeDisplaySettings(ref best, 0);
        if (result != 0) throw new Exception("couldn't change the resolution (" + result + ")");
        return Resolution();
    }

    public static void RestoreResolution() { ChangeDisplaySettings(IntPtr.Zero, 0); }

    static readonly int[] Steps = { 100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500 };

    static PATH_INFO FirstPath() {
        uint np, nm;
        const uint QDC_ONLY_ACTIVE_PATHS = 2;
        if (GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, out np, out nm) != 0) throw new Exception("GetDisplayConfigBufferSizes failed");
        var paths = new PATH_INFO[np];
        var modes = new MODE_INFO[nm];
        if (QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ref np, paths, ref nm, modes, IntPtr.Zero) != 0) throw new Exception("QueryDisplayConfig failed");
        return paths[0];
    }

    static DPI_GET Get(PATH_INFO path) {
        var get = new DPI_GET();
        get.header.type = -3;
        get.header.size = (uint)Marshal.SizeOf(typeof(DPI_GET));
        get.header.adapterId = path.source.adapterId;
        get.header.id = path.source.id;
        if (DisplayConfigGetDeviceInfo(ref get) != 0) throw new Exception("couldn't read the display's scaling");
        return get;
    }

    // "current,recommended,max" in percent.
    public static string Scaling() {
        var g = Get(FirstPath());
        int recommended = Math.Abs(g.minRel);
        return Steps[recommended + g.curRel] + "," + Steps[recommended] + "," + Steps[Math.Min(Steps.Length - 1, recommended + g.maxRel)];
    }

    public static void SetScaling(int percent) {
        var path = FirstPath();
        var g = Get(path);
        int recommended = Math.Abs(g.minRel);
        int wanted = Array.IndexOf(Steps, percent);
        if (wanted < 0) throw new Exception(percent + "% isn't a Windows scaling step");
        int rel = wanted - recommended;
        if (rel < g.minRel || rel > g.maxRel)
            throw new Exception(percent + "% is more than this display allows (" + Scaling() + "): give the VM a larger screen");
        var set = new DPI_SET();
        set.header.type = -4;
        set.header.size = (uint)Marshal.SizeOf(typeof(DPI_SET));
        set.header.adapterId = path.source.adapterId;
        set.header.id = path.source.id;
        set.scaleRel = rel;
        if (DisplayConfigSetDeviceInfo(ref set) != 0) throw new Exception("couldn't set the display's scaling");
    }
}
'@

function Close-Settings {
    # Opening a .theme file applies it and leaves Settings open in front.
    Start-Sleep -Seconds 4
    Get-Process SystemSettings -ErrorAction SilentlyContinue | Stop-Process -Force
}

function Set-AppsTheme([bool]$Dark) {
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
    $value = if ($Dark) { 0 } else { 1 }
    Set-ItemProperty $key -Name AppsUseLightTheme -Value $value -Type DWord
    Set-ItemProperty $key -Name SystemUsesLightTheme -Value $value -Type DWord
    [ProbeDisplay]::Broadcast('ImmersiveColorSet')
}

function Find-HcThemeFile([string]$Name) {
    $Name = $Name -replace '-', ' '  # night-sky: vmtest run can't pass a quoted space
    # Windows 11's High Contrast themes are named in the files only by
    # resource strings (themeui.dll,-2108), and the file names don't follow
    # the names (Aquatic is hcblack.theme), so each is found by its window
    # colour, which is what makes it that theme.
    $windowColour = @{ aquatic = '32 32 32'; desert = '255 250 239'; dusk = '45 50 54'; 'night sky' = '0 0 0' }
    if (-not $windowColour.ContainsKey($Name)) { throw "No High Contrast theme called '$Name' (aquatic, desert, dusk, night-sky)." }
    $folder = Join-Path $env:WINDIR 'Resources\Ease of Access Themes'
    foreach ($file in Get-ChildItem $folder -Filter *.theme) {
        $text = Get-Content $file.FullName -Raw
        if ($text -match '(?im)^Window=\s*(\d+\s+\d+\s+\d+)\s*$' -and ($Matches[1] -replace '\s+', ' ') -eq $windowColour[$Name]) {
            return $file.FullName
        }
    }
    throw "No theme in $folder has $Name's window colour ($($windowColour[$Name]))."
}

function Set-HighContrast([string]$Name) {
    if (-not $Name -and -not [ProbeDisplay]::HighContrastOn()) { return }
    $theme = if ($Name) { Find-HcThemeFile $Name } else { Join-Path $env:WINDIR 'Resources\Themes\aero.theme' }
    # Windows sometimes ignores the first switch (after a run that was
    # stopped part way, for one), so it gets a second try.
    foreach ($try in 1, 2) {
        Start-Process $theme
        Close-Settings
        $deadline = (Get-Date).AddSeconds(30)
        while ([ProbeDisplay]::HighContrastOn() -ne [bool]$Name -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
        if ([ProbeDisplay]::HighContrastOn() -eq [bool]$Name) { return }
    }
    throw "High Contrast didn't turn $(if ($Name) {'on'} else {'off'})."
}

function Set-Scaling([int]$Percent) {
    $current = [int](([ProbeDisplay]::Scaling()) -split ',')[0]
    if ($current -ne $Percent) {
        [ProbeDisplay]::SetScaling($Percent)
        Start-Sleep -Seconds 3
    }
}

function Reset-Display {
    Set-HighContrast ''
    Set-AppsTheme $false
    Set-Scaling 100
    [ProbeDisplay]::SetKeyboardCues($true)
    [ProbeDisplay]::SetClearType()
}

New-Item $Out -ItemType Directory -Force | Out-Null
"Display before: $([ProbeDisplay]::Resolution()), scaling (current,recommended,max) $([ProbeDisplay]::Scaling()); High Contrast $([ProbeDisplay]::HighContrastOn())"
$code = 1
try {
    "Resolution for the run: $([ProbeDisplay]::UseLargestResolution())"
    Start-Sleep -Seconds 2
    Reset-Display
    switch ($Theme) {
        'dark' { Set-AppsTheme $true }
        'high-contrast' { Set-HighContrast $HcTheme }
    }
    Set-Scaling $Scale
    "Variant $Tag set: scaling $([ProbeDisplay]::Scaling()); High Contrast $([ProbeDisplay]::HighContrastOn())"
    $probeArgs = @("$Source\tools\ui_probe.py", '--out', $Out, '--tag', $Tag)
    foreach ($name in $Surface) { $probeArgs += @('--surface', $name) }
    & $Python @probeArgs
    $code = $LASTEXITCODE
} finally {
    # The resolution goes back even if the theme couldn't.
    try { Reset-Display } finally { [ProbeDisplay]::RestoreResolution() }
}
exit $code
