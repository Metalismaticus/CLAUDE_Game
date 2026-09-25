<#
studio: проверка компьютера для /setup. Только чтение: ничего не ставит и не меняет.

    powershell -NoProfile -ExecutionPolicy Bypass -File <плагин>\tools\env_check.ps1 [-ProjectDir <папка проекта>]

Печатает на stdout JSON в UTF-8: система (ОС, процессор, видеокарты с
видеопамятью, память, основной монитор — частота сейчас и наибольшая при этом
разрешении, питание — сеть или батарея, план и режим питания), свободное место
на диске проекта; это и отпечаток машины для «Бюджета производительности»
docs/TESTING.md. Программы — git
(core.autocrlf, git-lfs), python (+ Pillow), dotnet, ffmpeg, Blender, gh,
winget; движки — Godot (PATH, WinGet, Program Files, steamapps\common, Рабочий
стол, Загрузки, Документы до глубины 3; и _console.exe, и оконный; версия через
--version), шаблоны экспорта Godot, Unity (Hub и редакторы), Unreal.

Чего не нашёл — "не удалось определить", а не «нет». У того, что ставится через
winget, — winget_id. Ошибка одного пункта пишется в errors и не мешает
остальным. Код возврата всегда 0.

Файл — UTF-8 с BOM, а код — только ASCII: Windows PowerShell 5.1 читает файл
без BOM в кодировке ANSI, поэтому русские строки вывода собраны из \u-кодов и
не сломаются, если BOM потеряется.
#>
param([string]$ProjectDir = '')

$ErrorActionPreference = 'Stop'
$started = Get-Date
$utf8 = New-Object System.Text.UTF8Encoding($false)
try { [Console]::OutputEncoding = $utf8 } catch { }

function U([string]$text) { [regex]::Unescape($text) }
$NF = U '\u043d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u043f\u0440\u0435\u0434\u0435\u043b\u0438\u0442\u044c'
$UNSET = U '\u043d\u0435 \u0437\u0430\u0434\u0430\u043d\u043e'

if (-not $ProjectDir) { $ProjectDir = (Get-Location).ProviderPath }
$errors = New-Object System.Collections.Generic.List[string]

# Пункт проверки: любая ошибка — строка в errors, остальное идёт дальше.
function Step([string]$stepName, [scriptblock]$stepBody) {
    try { & $stepBody | Out-Null } catch { $errors.Add(('{0}: {1} (line {2})' -f $stepName, $_.Exception.Message, $_.InvocationInfo.ScriptLineNumber)) }
}

# Запустить программу без окна, с пределом времени; $null — не ответила вовремя.
function Run([string]$exe, [string]$arguments, [int]$seconds = 15, [string]$cwd = '') {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exe
    $psi.Arguments = $arguments
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = $utf8
    $psi.StandardErrorEncoding = $utf8
    if ($cwd -and [System.IO.Directory]::Exists($cwd)) { $psi.WorkingDirectory = $cwd }
    $p = [System.Diagnostics.Process]::Start($psi)
    try { $p.StandardInput.Close() } catch { }
    $o = $p.StandardOutput.ReadToEndAsync()
    $e = $p.StandardError.ReadToEndAsync()
    if (-not $p.WaitForExit($seconds * 1000)) {
        try { $p.Kill() } catch { }
        return $null
    }
    [void]$o.Wait(3000); [void]$e.Wait(3000)
    $out = if ($o.IsCompleted) { $o.Result } else { '' }
    $err = if ($e.IsCompleted) { $e.Result } else { '' }
    [pscustomobject]@{ Code = $p.ExitCode; Out = $out; All = ($out + "`n" + $err) }
}

function Which([string]$command) {
    $c = Get-Command $command -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { $c.Source } else { $null }
}

function OrNF($value) { if ($null -eq $value -or "$value" -eq '') { $NF } else { $value } }

# Список или "не удалось определить", если пуст. Через конвейер: @() над List[object] со словарями
# в PowerShell 5.1 падает («Argument types do not match»); запятая сохраняет массив из одного.
function ListOrNF($items) { $a = @($items | Where-Object { $null -ne $_ }); if ($a.Count -gt 0) { , $a } else { $NF } }

function Tool([string]$path, [string]$version, [string]$wingetId) {
    $t = [ordered]@{ path = (OrNF $path); version = (OrNF $version) }
    if ($wingetId) { $t.winget_id = $wingetId }
    $t
}

# Файлы по маске не глубже $depth папок; ссылки-папки не обходятся (петли).
function Find-Files([string]$root, [string]$pattern, [int]$depth) {
    $found = New-Object System.Collections.Generic.List[string]
    if (-not $root -or -not [System.IO.Directory]::Exists($root)) { return , $found }
    $queue = New-Object System.Collections.Generic.Queue[object]
    $queue.Enqueue(@($root, 0))
    while ($queue.Count -gt 0) {
        $item = $queue.Dequeue()
        try { foreach ($f in [System.IO.Directory]::EnumerateFiles($item[0], $pattern)) { $found.Add($f) } } catch { }
        if ($item[1] -ge $depth) { continue }
        try {
            foreach ($d in [System.IO.Directory]::EnumerateDirectories($item[0])) {
                try {
                    if (([System.IO.File]::GetAttributes($d) -band [System.IO.FileAttributes]::ReparsePoint) -eq 0) {
                        $queue.Enqueue(@($d, ($item[1] + 1)))
                    }
                } catch { }
            }
        } catch { }
    }
    return , $found
}

function VersionKey([string]$text) {
    if ($text -match '(\d+)\.(\d+)(?:\.(\d+))?') {
        $patch = if ($Matches[3]) { [int]$Matches[3] } else { 0 }
        return New-Object System.Version([int]$Matches[1], [int]$Matches[2], $patch)
    }
    New-Object System.Version(0, 0)
}

function FileVersion([string]$path) {
    try {
        $v = (Get-Item -LiteralPath $path).VersionInfo
        if ($v.ProductVersion) { return $v.ProductVersion.Trim() }
        if ($v.FileVersion) { return $v.FileVersion.Trim() }
    } catch { }
    $null
}

function SteamCommon {
    $libs = New-Object System.Collections.Generic.List[string]
    $steam = $null
    try { $steam = (Get-ItemProperty 'HKCU:\Software\Valve\Steam' -ErrorAction Stop).SteamPath } catch { }
    if (-not $steam) { $steam = Join-Path ${env:ProgramFiles(x86)} 'Steam' }
    $steam = $steam -replace '/', '\'
    $libs.Add($steam)
    $vdf = Join-Path $steam 'steamapps\libraryfolders.vdf'
    if (Test-Path -LiteralPath $vdf) {
        $text = [System.IO.File]::ReadAllText($vdf, $utf8)
        foreach ($m in [regex]::Matches($text, '"path"\s+"([^"]+)"')) { $libs.Add(($m.Groups[1].Value -replace '\\\\', '\')) }
    }
    $result = New-Object System.Collections.Generic.List[string]
    foreach ($lib in ($libs | Select-Object -Unique)) {
        $common = Join-Path $lib 'steamapps\common'
        if ([System.IO.Directory]::Exists($common)) { $result.Add($common) }
    }
    return , $result
}

function Downloads {
    try {
        $p = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders' -ErrorAction Stop).'{374DE290-123F-4565-9164-39C4925E467B}'
        if ($p) { return $p }
    } catch { }
    Join-Path $env:USERPROFILE 'Downloads'
}

# ---------------------------------------------------------------- заготовка
$tools = [ordered]@{
    git     = (Tool $null $null 'Git.Git')
    git_lfs = (Tool $null $null 'GitHub.GitLFS')
    python  = (Tool $null $null 'Python.Python.3.13')
    pillow  = [ordered]@{ version = $NF; install = 'python -m pip install pillow' }
    dotnet  = (Tool $null $null 'Microsoft.DotNet.SDK.8')
    ffmpeg  = (Tool $null $null 'Gyan.FFmpeg')
    blender = (Tool $null $null 'BlenderFoundation.Blender')
    gh      = (Tool $null $null 'GitHub.cli')
    winget  = [ordered]@{ path = $NF; version = $NF; install = 'Microsoft Store: App Installer' }
}
$tools.git.core_autocrlf = $NF
$godot = [ordered]@{
    found            = $NF
    best             = $NF
    best_dotnet      = $NF
    export_templates = [ordered]@{ path = (Join-Path $env:APPDATA 'Godot\export_templates'); versions = $NF; match_best = $NF }
    winget_id        = 'GodotEngine.GodotEngine'
    winget_id_dotnet = 'GodotEngine.GodotEngine.Mono'
}
$unity = [ordered]@{ hub = $NF; editors = $NF; winget_id = 'Unity.UnityHub' }
$unreal = [ordered]@{ installs = $NF; launcher = $NF; winget_id = 'EpicGames.EpicGamesLauncher' }
$system = [ordered]@{ os = $NF; os_version = $NF; cpu = $NF; cores = $NF; threads = $NF; ram_gb = $NF; gpu = $NF; monitor = $NF; power = $NF }
$project = [ordered]@{
    path      = $ProjectDir
    non_ascii = [bool]($ProjectDir -match '[^\u0000-\u007F]')
    spaces    = $ProjectDir.Contains(' ')
    drive     = $NF
    free_gb   = $NF
    total_gb  = $NF
}

# ---------------------------------------------------------------- система
Step 'os' {
    $os = Get-CimInstance Win32_OperatingSystem
    $display = $null
    try { $display = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -ErrorAction Stop).DisplayVersion } catch { }
    $system.os = ('{0} {1}' -f $os.Caption.Trim(), $os.OSArchitecture).Trim()
    $system.os_version = if ($display) { '{0} ({1})' -f $os.Version, $display } else { $os.Version }
    $system.ram_gb = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
}
Step 'cpu' {
    $cpu = @(Get-CimInstance Win32_Processor)[0]
    $system.cpu = $cpu.Name.Trim()
    $system.cores = [int]$cpu.NumberOfCores
    $system.threads = [int]$cpu.NumberOfLogicalProcessors
}
Step 'ram' {
    $cs = Get-CimInstance Win32_ComputerSystem
    $system.ram_gb = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
}
Step 'gpu' {
    $vram = @{}
    $class = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
    foreach ($key in @(Get-ChildItem $class -ErrorAction SilentlyContinue)) {
        if ($key.PSChildName -notmatch '^\d{4}$') { continue }
        try {
            $p = Get-ItemProperty $key.PSPath -ErrorAction Stop
            $size = $p.'HardwareInformation.qwMemorySize'
            if (-not $size) {
                $raw = $p.'HardwareInformation.MemorySize'
                if ($raw -is [byte[]] -and $raw.Length -ge 4) { $size = [BitConverter]::ToUInt32($raw, 0) } elseif ($raw) { $size = $raw }
            }
            if ($size -and $p.DriverDesc) { $vram[$p.DriverDesc] = [double]$size }
        } catch { }
    }
    $list = foreach ($g in @(Get-CimInstance Win32_VideoController)) {
        $from = $NF
        $bytes = if ($vram.ContainsKey($g.Name)) { $from = 'registry'; $vram[$g.Name] } elseif ($g.AdapterRAM -gt 0) { $from = 'WMI AdapterRAM, at most 4 GB'; [double]$g.AdapterRAM } else { $null }
        $item = [ordered]@{
            name      = $g.Name
            driver    = (OrNF $g.DriverVersion)
            vram_gb   = $(if ($bytes) { [math]::Round($bytes / 1GB, 1) } else { $NF })
            vram_mb   = $(if ($bytes) { [int][math]::Round($bytes / 1MB) } else { $NF })
            vram_from = $from
        }
        # У NVIDIA номер драйвера — последние пять цифр версии Windows: 32.0.15.9597 -> 595.97.
        $digits = "$($g.DriverVersion)" -replace '\D', ''
        if ($g.Name -match '(?i)nvidia' -and $digits.Length -ge 5) {
            $tail = $digits.Substring($digits.Length - 5)
            $item.driver_nvidia = '{0}.{1}' -f $tail.Substring(0, 3), $tail.Substring(3)
        }
        $item
    }
    $system.gpu = ListOrNF $list
}
Step 'monitor' {
    $mode = $null
    try {
        if (-not ('StudioDisplay' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class StudioDisplay {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct DEVMODE {
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmDeviceName;
        public short dmSpecVersion; public short dmDriverVersion; public short dmSize; public short dmDriverExtra;
        public int dmFields; public int dmPositionX; public int dmPositionY; public int dmDisplayOrientation; public int dmDisplayFixedOutput;
        public short dmColor; public short dmDuplex; public short dmYResolution; public short dmTTOption; public short dmCollate;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmFormName;
        public short dmLogPixels; public int dmBitsPerPel; public int dmPelsWidth; public int dmPelsHeight;
        public int dmDisplayFlags; public int dmDisplayFrequency;
        public int dmICMMethod; public int dmICMIntent; public int dmMediaType; public int dmDitherType;
        public int dmReserved1; public int dmReserved2; public int dmPanningWidth; public int dmPanningHeight;
    }
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern bool EnumDisplaySettings(string deviceName, int modeNum, ref DEVMODE devMode);
    public static int[] Primary() {
        DEVMODE d = new DEVMODE();
        d.dmSize = (short)Marshal.SizeOf(typeof(DEVMODE));
        if (!EnumDisplaySettings(null, -1, ref d)) return null;
        return new int[] { d.dmPelsWidth, d.dmPelsHeight, d.dmDisplayFrequency };
    }
    public static int MaxHz(int width, int height) {
        int best = 0;
        DEVMODE d = new DEVMODE();
        d.dmSize = (short)Marshal.SizeOf(typeof(DEVMODE));
        for (int i = 0; i < 4096 && EnumDisplaySettings(null, i, ref d); i++) {
            if (d.dmPelsWidth == width && d.dmPelsHeight == height && d.dmDisplayFrequency > best) best = d.dmDisplayFrequency;
        }
        return best;
    }
}
'@
        }
        $mode = [StudioDisplay]::Primary()
    } catch { }
    if ($mode -and $mode[2] -gt 1) {
        $system.monitor = [ordered]@{ width = $mode[0]; height = $mode[1]; refresh_hz = $mode[2]; max_hz = $NF; how = 'EnumDisplaySettings' }
        try { $max = [StudioDisplay]::MaxHz($mode[0], $mode[1]); if ($max -gt 1) { $system.monitor.max_hz = $max } } catch { }
        return
    }
    $g = @(Get-CimInstance Win32_VideoController | Where-Object { $_.CurrentRefreshRate -gt 1 })[0]
    if ($g) {
        $system.monitor = [ordered]@{ width = $g.CurrentHorizontalResolution; height = $g.CurrentVerticalResolution; refresh_hz = $g.CurrentRefreshRate; how = 'Win32_VideoController' }
    }
}
# Питание: сеть или батарея, план (имя — английское из реестра) и режим Windows 11 для текущего источника.
Step 'power' {
    $power = [ordered]@{ source = $NF; battery = $NF; plan = $NF; plan_guid = $NF; mode = $NF }
    $system.power = $power
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $s = [System.Windows.Forms.SystemInformation]::PowerStatus
        $power.source = switch ("$($s.PowerLineStatus)") { 'Online' { 'AC' } 'Offline' { 'battery' } default { $NF } }
        $power.battery = if ("$($s.BatteryChargeStatus)" -match 'NoSystemBattery') { 'none' } else { '{0}%' -f [int]($s.BatteryLifePercent * 100) }
    } catch {
        if (@(Get-CimInstance Win32_Battery).Count -eq 0) { $power.source = 'AC'; $power.battery = 'none' }
    }
    $schemes = 'HKLM:\SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes'
    $p = Get-ItemProperty $schemes -ErrorAction Stop
    if ($p.ActivePowerScheme) {
        $power.plan_guid = $p.ActivePowerScheme
        $name = $null
        try { $name = (Get-ItemProperty (Join-Path $schemes $p.ActivePowerScheme) -ErrorAction Stop).FriendlyName } catch { }
        if ($name) { $power.plan = ($name -split ',')[-1].Trim() }
    }
    $overlay = if ($power.source -eq 'battery') { $p.ActiveOverlayDcPowerScheme } else { $p.ActiveOverlayAcPowerScheme }
    $power.mode = switch ("$overlay".ToLowerInvariant()) {
        'ded574b5-45a0-4f42-8737-46345c09c238' { 'best performance' }
        '3af9b8d9-7c97-431d-ad78-34a8bfea439f' { 'better performance' }
        '961cc777-2547-4f9d-8174-7d86181b8a7a' { 'best power efficiency' }
        { $_ -eq '' -or $_ -eq '00000000-0000-0000-0000-000000000000' } { 'balanced' }
        default { "$overlay" }
    }
}
Step 'disk' {
    $root = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($ProjectDir))
    $d = New-Object System.IO.DriveInfo($root)
    $project.drive = $d.Name
    if (-not $d.IsReady) { throw "disk $root is not ready" }
    $project.free_gb = [math]::Round($d.AvailableFreeSpace / 1GB, 1)
    $project.total_gb = [math]::Round($d.TotalSize / 1GB, 1)
}

# ---------------------------------------------------------------- программы
Step 'git' {
    $exe = Which 'git'
    if (-not $exe) { return }
    $r = Run $exe '--version'
    $v = if ($r -and $r.Out -match 'git version\s+(\S+)') { $Matches[1] } else { $null }
    $tools.git = Tool $exe $v 'Git.Git'
    $tools.git.core_autocrlf = $NF
    $r = Run $exe '-c core.quotePath=false config --show-origin --get core.autocrlf' 15 $ProjectDir
    if ($r -and $r.Code -eq 0 -and $r.Out.Trim()) {
        $parts = $r.Out.Trim() -split "`t", 2
        $tools.git.core_autocrlf = $parts[-1].Trim()
        $tools.git.core_autocrlf_from = ($parts[0] -replace '^file:', '').Trim('"')
    } elseif ($r -and $r.Code -eq 1) {
        $tools.git.core_autocrlf = $UNSET
    }
    $r = Run $exe 'lfs version'
    if ($r -and $r.Code -eq 0 -and $r.Out -match 'git-lfs/(\S+)') {
        $tools.git_lfs = Tool "$exe lfs" $Matches[1] 'GitHub.GitLFS'
    }
}
Step 'python' {
    $found = $null; $ver = $null
    $candidates = @(Get-Command python -CommandType Application -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
    foreach ($exe in $candidates) {
        $r = $null
        try { $r = Run $exe '--version' } catch { }
        if ($r -and $r.All -match 'Python\s+(3\.\d+\.\d+)') { $found = $exe; $ver = $Matches[1]; break }
    }
    if (-not $found) {
        $py = Which 'py'
        if ($py) {
            $r = Run $py '-3 -c "import sys, platform; print(sys.executable); print(platform.python_version())"'
            if ($r -and $r.Code -eq 0) {
                $lines = @($r.Out -split "`r?`n" | Where-Object { $_.Trim() })
                if ($lines.Count -ge 2) { $found = $lines[0].Trim(); $ver = $lines[1].Trim() }
            }
        }
    }
    if (-not $found) { return }
    $tools.python = Tool $found $ver 'Python.Python.3.13'
    $r = Run $found '-c "import PIL; print(PIL.__version__)"'
    if ($r -and $r.Code -eq 0 -and $r.Out.Trim()) { $tools.pillow.version = $r.Out.Trim() }
}
Step 'dotnet' {
    $exe = Which 'dotnet'
    if (-not $exe) { return }
    $r = Run $exe '--list-sdks'
    $sdks = @()
    if ($r) { $sdks = @([regex]::Matches($r.Out, '(?m)^(\d+\.\d+\.\d+\S*)') | ForEach-Object { $_.Groups[1].Value }) }
    $last = if ($sdks.Count) { $sdks[-1] } else { $null }
    $tools.dotnet = Tool $exe $last 'Microsoft.DotNet.SDK.8'
    $tools.dotnet.sdks = ListOrNF $sdks
}
Step 'ffmpeg' {
    $exe = Which 'ffmpeg'
    if (-not $exe) { return }
    $r = Run $exe '-version'
    $v = if ($r -and $r.Out -match 'ffmpeg version\s+(\S+)') { $Matches[1] } else { $null }
    $tools.ffmpeg = Tool $exe $v 'Gyan.FFmpeg'
}
Step 'gh' {
    $exe = Which 'gh'
    if (-not $exe) { return }
    $r = Run $exe '--version'
    $v = if ($r -and $r.Out -match 'gh version\s+(\S+)') { $Matches[1] } else { $null }
    $tools.gh = Tool $exe $v 'GitHub.cli'
}
Step 'winget' {
    $exe = Which 'winget'
    if (-not $exe) { return }
    $r = Run $exe '--version'
    $tools.winget.path = $exe
    if ($r -and $r.Out -match 'v?(\d+\.\d+[\d.]*)') { $tools.winget.version = $Matches[1] }
}
Step 'blender' {
    $exe = Which 'blender'
    if (-not $exe) {
        $roots = @(Join-Path $env:ProgramFiles 'Blender Foundation')
        foreach ($common in (SteamCommon)) { $roots += Join-Path $common 'Blender' }
        $all = foreach ($root in $roots) { foreach ($f in (Find-Files $root 'blender.exe' 2)) { $f } }
        $exe = @($all | Sort-Object { VersionKey ((Split-Path $_ -Parent) + ' ' + (FileVersion $_)) } -Descending)[0]
    }
    if (-not $exe) { return }
    $v = FileVersion $exe
    if (-not $v -and (Split-Path $exe -Parent) -match '(\d+\.\d+)') { $v = $Matches[1] }
    $tools.blender = Tool $exe $v 'BlenderFoundation.Blender'
}

# ---------------------------------------------------------------- Godot
# Самая новая версия из списка и её пара (оконный / консольный) из той же папки.
function Pick($choices, $all) {
    $known = @($choices | Where-Object { $_.version -ne $NF })
    if ($known.Count -eq 0) { $known = @($choices | ForEach-Object { $_ }) }
    if ($known.Count -eq 0) { return $NF }
    $top = @($known | Sort-Object { VersionKey $_.version } -Descending)[0]
    $dir = Split-Path $top.path -Parent
    $same = @($all | Where-Object { (Split-Path $_.path -Parent) -eq $dir })
    $window = @($same | Where-Object { $_.kind -eq 'window' })[0]
    $console = @($same | Where-Object { $_.kind -eq 'console' })[0]
    [ordered]@{
        version = $top.version
        window  = $(if ($window) { $window.path } else { $NF })
        console = $(if ($console) { $console.path } else { $NF })
        mono    = $top.mono
    }
}

Step 'godot' {
    $places = New-Object System.Collections.Generic.List[object]
    foreach ($c in @(Get-Command 'godot*' -CommandType Application -All -ErrorAction SilentlyContinue)) {
        if ($c.Source -like '*.exe') { $places.Add(@('PATH', $c.Source)) }
    }
    $winget = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet'
    foreach ($f in (Find-Files (Join-Path $winget 'Links') 'godot*.exe' 0)) { $places.Add(@('WinGet', $f)) }
    foreach ($f in (Find-Files (Join-Path $winget 'Packages') 'godot*.exe' 3)) { $places.Add(@('WinGet', $f)) }
    $roots = @(
        @('Program Files', $env:ProgramFiles), @('Program Files', ${env:ProgramFiles(x86)}),
        @('Programs', (Join-Path $env:LOCALAPPDATA 'Programs')),
        @('Desktop', [Environment]::GetFolderPath('Desktop')), @('Downloads', (Downloads)),
        @('Documents', [Environment]::GetFolderPath('MyDocuments'))
    )
    foreach ($common in (SteamCommon)) { $roots += , @('Steam', $common) }
    foreach ($dir in @(Get-ChildItem ($env:SystemDrive + '\') -Directory -Filter '*godot*' -ErrorAction SilentlyContinue)) {
        $roots += , @('disk root', $dir.FullName)
    }
    foreach ($r in $roots) { foreach ($f in (Find-Files $r[1] 'godot*.exe' 3)) { $places.Add(@($r[0], $f)) } }

    $seen = @{}
    $found = New-Object System.Collections.Generic.List[object]
    foreach ($place in $places) {
        $path = $place[1]
        try {
            $item = Get-Item -LiteralPath $path -ErrorAction Stop
            $target = @($item.Target)[0]
            if ($target) {
                if (-not [System.IO.Path]::IsPathRooted($target)) { $target = Join-Path (Split-Path $path -Parent) $target }
                $path = [System.IO.Path]::GetFullPath($target)
            }
        } catch { }
        $key = $path.ToLowerInvariant()
        if ($seen.ContainsKey($key) -or -not [System.IO.File]::Exists($path) -or $found.Count -ge 12) { continue }
        $seen[$key] = $true
        $name = [System.IO.Path]::GetFileName($path)
        $version = $null; $from = $NF
        $r = $null
        try { $r = Run $path '--version' 20 } catch { }
        if ($r) {
            $line = @($r.All -split "`r?`n" | Where-Object { $_ -match '^\s*\d+\.\d+' } | Select-Object -Last 1)[0]
            if ($line) { $version = $line.Trim(); $from = '--version' }
        }
        if (-not $version -and $name -match '(?i)v(\d+\.\d+(?:\.\d+)?)-([a-z]+\d*)') {
            $version = '{0}.{1}' -f $Matches[1], $Matches[2]
            if ($name -match '(?i)mono') { $version += '.mono' }
            $from = 'file name'
        }
        $found.Add([ordered]@{
            path         = $path
            kind         = $(if ($name -match '(?i)_console\.exe$') { 'console' } else { 'window' })
            mono         = [bool]($path -match '(?i)mono')
            version      = (OrNF $version)
            version_from = $from
            source       = $place[0]
        })
    }
    if ($found.Count -eq 0) { return }
    $godot.found = ListOrNF $found
    # best — обычная сборка (GDScript), best_dotnet — сборка .NET (C#); у каждой оконный и _console.exe рядом.
    $plain = @($found | Where-Object { -not $_.mono })
    if ($plain.Count -eq 0) { $plain = @($found | ForEach-Object { $_ }) }
    $godot.best = Pick $plain $found
    $godot.best_dotnet = Pick @($found | Where-Object { $_.mono }) $found
}
Step 'godot export templates' {
    $dir = $godot.export_templates.path
    if (-not [System.IO.Directory]::Exists($dir)) { return }
    $names = @(Get-ChildItem -LiteralPath $dir -Directory -ErrorAction SilentlyContinue |
        Where-Object { @(Get-ChildItem -LiteralPath $_.FullName -File -ErrorAction SilentlyContinue).Count -gt 0 } |
        ForEach-Object { $_.Name })
    $godot.export_templates.versions = ListOrNF $names
    foreach ($which in @('best', 'best_dotnet')) {
        $b = $godot[$which]
        if ($b -is [System.Collections.IDictionary] -and $b.version -match '^(\d+(?:\.\d+){1,2}\.[a-z]+\d*)') {
            $tag = $Matches[1]
            if ($b.mono) { $tag += '.mono' }
            $suffix = if ($which -eq 'best') { 'best' } else { 'dotnet' }
            $godot.export_templates["need_$suffix"] = $tag
            $godot.export_templates["match_$suffix"] = ($names -contains $tag)
        }
    }
}

# ---------------------------------------------------------------- Unity, Unreal
Step 'unity' {
    $hub = Join-Path $env:ProgramFiles 'Unity Hub\Unity Hub.exe'
    if ([System.IO.File]::Exists($hub)) { $unity.hub = [ordered]@{ path = $hub; version = (OrNF (FileVersion $hub)) } }
    $roots = @((Join-Path $env:ProgramFiles 'Unity\Hub\Editor'))
    $secondary = Join-Path $env:APPDATA 'UnityHub\secondaryInstallPath.json'
    if (Test-Path -LiteralPath $secondary) {
        $extra = ([System.IO.File]::ReadAllText($secondary, $utf8)).Trim().Trim('"') -replace '\\\\', '\'
        if ($extra) { $roots += $extra }
    }
    $editors = foreach ($root in $roots) {
        foreach ($exe in (Find-Files $root 'Unity.exe' 2)) {
            if ((Split-Path $exe -Parent | Split-Path -Leaf) -ne 'Editor') { continue }
            [ordered]@{ version = (Split-Path (Split-Path (Split-Path $exe -Parent) -Parent) -Leaf); path = $exe }
        }
    }
    $solo = Join-Path $env:ProgramFiles 'Unity\Editor\Unity.exe'
    if ([System.IO.File]::Exists($solo)) { $editors = @($editors | Where-Object { $_ }) + @([ordered]@{ version = (OrNF (FileVersion $solo)); path = $solo }) }
    $unity.editors = ListOrNF $editors
}
Step 'unreal' {
    $dirs = New-Object System.Collections.Generic.List[string]
    $dat = Join-Path $env:ProgramData 'Epic\UnrealEngineLauncher\LauncherInstalled.dat'
    if (Test-Path -LiteralPath $dat) {
        $list = ([System.IO.File]::ReadAllText($dat, $utf8) | ConvertFrom-Json).InstallationList
        foreach ($i in @($list)) { if ($i.AppName -like 'UE_*') { $dirs.Add($i.InstallLocation) } }
    }
    foreach ($key in @(Get-ChildItem 'HKLM:\SOFTWARE\EpicGames\Unreal Engine' -ErrorAction SilentlyContinue)) {
        try { $d = (Get-ItemProperty $key.PSPath -ErrorAction Stop).InstalledDirectory; if ($d) { $dirs.Add($d) } } catch { }
    }
    foreach ($d in @(Get-ChildItem (Join-Path $env:ProgramFiles 'Epic Games') -Directory -Filter 'UE_*' -ErrorAction SilentlyContinue)) { $dirs.Add($d.FullName) }
    $seen = @{}
    $installs = foreach ($d in $dirs) {
        $key = $d.TrimEnd('\').ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        $exe = Join-Path $d 'Engine\Binaries\Win64\UnrealEditor.exe'
        if (-not [System.IO.File]::Exists($exe)) { $exe = Join-Path $d 'Engine\Binaries\Win64\UE4Editor.exe' }
        if (-not [System.IO.File]::Exists($exe)) { continue }
        $version = $null
        $build = Join-Path $d 'Engine\Build\Build.version'
        if (Test-Path -LiteralPath $build) {
            $b = [System.IO.File]::ReadAllText($build, $utf8) | ConvertFrom-Json
            $version = '{0}.{1}.{2}' -f $b.MajorVersion, $b.MinorVersion, $b.PatchVersion
        }
        [ordered]@{ version = (OrNF $version); path = $exe }
    }
    $unreal.installs = ListOrNF $installs
    $launcher = Join-Path ${env:ProgramFiles(x86)} 'Epic Games\Launcher\Portal\Binaries\Win64\EpicGamesLauncher.exe'
    if ([System.IO.File]::Exists($launcher)) { $unreal.launcher = $launcher }
}

# ---------------------------------------------------------------- вывод
$result = [ordered]@{
    checker = 'studio env_check'
    checked = (Get-Date -Format 'yyyy-MM-dd HH:mm')
    project = $project
    system  = $system
    tools   = $tools
    engines = [ordered]@{ godot = $godot; unity = $unity; unreal = $unreal }
    errors  = @($errors | ForEach-Object { $_ })
    seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
}
$json = ConvertTo-Json -InputObject $result -Depth 8
$bytes = $utf8.GetBytes($json + "`n")
$stdout = [Console]::OpenStandardOutput()
$stdout.Write($bytes, 0, $bytes.Length)
$stdout.Flush()
exit 0
