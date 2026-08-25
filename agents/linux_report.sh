#!/usr/bin/env bash
# MHS Dashboard – Linux device stats reporter
#
# Posts CPU / RAM / disk / uptime stats to the Pi dashboard so the
# "Remote Devices" screen can display them.
#
# Configuration -------------------------------------------------------
PI_IP="192.168.1.100"   # Change to your Pi's IP address
PORT="8080"
DEVICE_ID="$(hostname)"        # Unique ID for this device
DEVICE_NAME="$(hostname)"      # Human-readable display name
DEVICE_TYPE="linux"            # linux | windows | android
# Optional: set MHS_CONFIG_TOKEN env-var if your server requires it.
TOKEN="${MHS_CONFIG_TOKEN:-}"
# ---------------------------------------------------------------------

ENDPOINT="http://${PI_IP}:${PORT}/api/devices/report"

# --- CPU usage (1-second sample) -------------------------------------
cpu_idle_before=$(awk '/^cpu /{print $5+$6}' /proc/stat)
cpu_total_before=$(awk '/^cpu /{s=0; for(i=2;i<=NF;i++) s+=$i; print s}' /proc/stat)
sleep 1
cpu_idle_after=$(awk '/^cpu /{print $5+$6}' /proc/stat)
cpu_total_after=$(awk '/^cpu /{s=0; for(i=2;i<=NF;i++) s+=$i; print s}' /proc/stat)

idle_delta=$(( cpu_idle_after - cpu_idle_before ))
total_delta=$(( cpu_total_after - cpu_total_before ))
if [ "$total_delta" -gt 0 ]; then
    cpu=$(awk "BEGIN {printf \"%.1f\", 100 * (1 - $idle_delta / $total_delta)}")
else
    cpu="0.0"
fi

# --- RAM (GB) ---------------------------------------------------------
ram_total_kb=$(awk '/^MemTotal/{print $2}' /proc/meminfo)
ram_avail_kb=$(awk '/^MemAvailable/{print $2}' /proc/meminfo)
ram_used_kb=$(( ram_total_kb - ram_avail_kb ))
ram_used=$(awk "BEGIN {printf \"%.2f\", $ram_used_kb / 1048576}")
ram_total=$(awk "BEGIN {printf \"%.2f\", $ram_total_kb / 1048576}")

# --- Disk (GB, root filesystem) ---------------------------------------
read -r disk_used_kb disk_total_kb <<< "$(df -k / | awk 'NR==2{print $3, $2}')"
disk_used=$(awk "BEGIN {printf \"%.1f\", $disk_used_kb / 1048576}")
disk_total=$(awk "BEGIN {printf \"%.1f\", $disk_total_kb / 1048576}")

# --- Uptime (seconds) -------------------------------------------------
uptime_s=$(awk '{print int($1)}' /proc/uptime)

# --- Timestamp --------------------------------------------------------
ts=$(date +%s)

# --- POST -------------------------------------------------------------
PAYLOAD=$(cat <<EOF
{
  "device_id":   "${DEVICE_ID}",
  "name":        "${DEVICE_NAME}",
  "type":        "${DEVICE_TYPE}",
  "cpu":         ${cpu},
  "ram_used":    ${ram_used},
  "ram_total":   ${ram_total},
  "disk_used":   ${disk_used},
  "disk_total":  ${disk_total},
  "uptime_s":    ${uptime_s},
  "timestamp":   ${ts}
}
EOF
)

CURL_ARGS=(-s -X POST -H "Content-Type: application/json" -d "$PAYLOAD")
if [ -n "$TOKEN" ]; then
    CURL_ARGS+=(-H "X-Config-Token: ${TOKEN}")
fi

curl --fail --connect-timeout 5 --max-time 15 "${CURL_ARGS[@]}" "$ENDPOINT" > /dev/null
