# MHS Dashboard – Windows device stats reporter (PowerShell)
#
# Posts CPU / RAM / disk / uptime stats to the Pi dashboard so the
# "Remote Devices" screen can display them.
#
# How to run periodically:
#   1. Open Task Scheduler → Create Task.
#   2. General: run whether logged on or not, run with highest privileges.
#   3. Triggers: On a schedule, repeat every 30 seconds indefinitely.
#      (Create a daily trigger, then set "Repeat task every: 30 minutes"
#       and change the minute entry to 30 seconds via the XML editor —
#       Task Scheduler GUI minimum is 5 minutes; edit the XML directly
#       and set <Repetition><Interval>PT30S</Interval></Repetition>.)
#   4. Actions: Start a program
#          Program:   powershell.exe
#          Arguments: -NonInteractive -ExecutionPolicy Bypass -File "C:\Path\windows_report.ps1"
#   5. Conditions: uncheck "Start only if the computer is on AC power".
# -----------------------------------------------------------------------

# ---- Configuration ----------------------------------------------------
$PiIP      = "192.168.1.100"   # Change to your Pi's IP address
$Port      = 8080
$DeviceId  = $env:COMPUTERNAME
$DeviceName= $env:COMPUTERNAME
$DeviceType= "windows"
$Token     = $env:MHS_CONFIG_TOKEN   # Optional; set env-var if server requires it
# -----------------------------------------------------------------------

$Endpoint = "http://${PiIP}:${Port}/api/devices/report"

# --- CPU (average over 1 second) ---------------------------------------
$CpuPct = (Get-CimInstance -ClassName Win32_Processor |
    Measure-Object -Property LoadPercentage -Average).Average

# --- RAM (GB) ----------------------------------------------------------
$Os = Get-CimInstance -ClassName Win32_OperatingSystem
$RamTotalGB  = [math]::Round($Os.TotalVisibleMemorySize / 1MB, 2)
$RamFreeGB   = [math]::Round($Os.FreePhysicalMemory      / 1MB, 2)
$RamUsedGB   = [math]::Round($RamTotalGB - $RamFreeGB, 2)

# --- Disk (GB, system drive) ------------------------------------------
$Disk = Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DeviceID='$env:SystemDrive'"
$DiskTotalGB = [math]::Round($Disk.Size           / 1GB, 1)
$DiskUsedGB  = [math]::Round(($Disk.Size - $Disk.FreeSpace) / 1GB, 1)

# --- Uptime (seconds) -------------------------------------------------
$BootTime  = (Get-CimInstance -ClassName Win32_OperatingSystem).LastBootUpTime
$UptimeS   = [int](((Get-Date) - $BootTime).TotalSeconds)

# --- Timestamp ---------------------------------------------------------
$Timestamp = [int][double]::Parse((Get-Date -UFormat "%s"))

# --- Build payload and POST -------------------------------------------
$Payload = [PSCustomObject]@{
    device_id   = $DeviceId
    name        = $DeviceName
    type        = $DeviceType
    cpu         = [double]$CpuPct
    ram_used    = $RamUsedGB
    ram_total   = $RamTotalGB
    disk_used   = $DiskUsedGB
    disk_total  = $DiskTotalGB
    uptime_s    = $UptimeS
    timestamp   = $Timestamp
} | ConvertTo-Json -Compress

$Headers = @{ "Content-Type" = "application/json" }
if ($Token) { $Headers["X-Config-Token"] = $Token }

try {
    Invoke-RestMethod -Uri $Endpoint -Method Post -Body $Payload -Headers $Headers -TimeoutSec 5 | Out-Null
} catch {
    # Silently ignore network errors so the script doesn't pop up error dialogs.
}
