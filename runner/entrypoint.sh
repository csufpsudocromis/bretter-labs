#!/usr/bin/env bash
set -euo pipefail

DISK=""
CONSOLE_WS=""
DISK_FORMAT_ENV="${DISK_FORMAT:-}"
OS_TYPE="${OS_TYPE:-windows}"
MACHINE_TYPE="${MACHINE_TYPE:-q35}"
EFI_ENABLED="${EFI_ENABLED:-false}"
CPU_MODEL="${CPU_MODEL:-host}"
VM_NET_BACKEND="${VM_NET_BACKEND:-user}"
VM_VHOST_NET_ENABLED="${VM_VHOST_NET_ENABLED:-true}"
VM_NET_MULTIQUEUE_ENABLED="${VM_NET_MULTIQUEUE_ENABLED:-true}"
CONSOLE_PROVIDER="${CONSOLE_PROVIDER:-spice}"
SPICE_TICKETING="${SPICE_TICKETING:-true}"
SPICE_PASSWORD="${SPICE_PASSWORD:-}"
RDP_FORWARD_PORT="${RDP_FORWARD_PORT:-33890}"
GUAC_TOKEN_KEY="${GUAC_TOKEN_KEY:-}"
# Default to NLA for Windows guests; allow overrides via env when required.
GUAC_RDP_SECURITY="${GUAC_RDP_SECURITY:-nla}"
GUAC_RDP_IGNORE_CERT="${GUAC_RDP_IGNORE_CERT:-true}"
BOOT_ISO="${BOOT_ISO:-}"
BOOT_ORDER="${BOOT_ORDER:-c}"
TAP_EGRESS_IF=""
VM_TPM_ENABLED="${VM_TPM_ENABLED:-auto}"
VM_SECURE_BOOT="${VM_SECURE_BOOT:-auto}"
AUTO_BOOT_ISO_KEYPRESS="${AUTO_BOOT_ISO_KEYPRESS:-auto}"
AUTO_BOOT_KEYPRESS_COUNT="${AUTO_BOOT_KEYPRESS_COUNT:-15}"
AUTO_BOOT_KEYPRESS_INTERVAL_SECONDS="${AUTO_BOOT_KEYPRESS_INTERVAL_SECONDS:-1}"
UEFI_MARKER_SCAN_MAX_BYTES="${UEFI_MARKER_SCAN_MAX_BYTES:-67108864}"
UEFI_FALLBACK_TO_BIOS_ON_NO_MARKER="${UEFI_FALLBACK_TO_BIOS_ON_NO_MARKER:-false}"
ISO_BOOT_RECORD_SCAN_MAX_SECTORS="${ISO_BOOT_RECORD_SCAN_MAX_SECTORS:-512}"
USE_VIRTUAL_TPM="0"
USE_SECURE_BOOT="0"
ENABLE_AUTO_BOOT_KEYPRESS="0"
SECONDARY_ISO=""
TPM_SOCKET_DIR="/tmp/swtpm"
TPM_STATE_DIR="/tmp/swtpm-state"
TPM_SOCKET_PATH="${TPM_SOCKET_DIR}/swtpm-sock"
QEMU_MONITOR_SOCKET="/tmp/qemu-monitor.sock"

is_enabled_flag() {
  local raw="${1:-}"
  case "${raw,,}" in
    1|true|yes|on)
      echo "1"
      ;;
    auto|"")
      if [[ "${OS_TYPE,,}" == "windows" ]]; then
        echo "1"
      else
        echo "0"
      fi
      ;;
    *)
      echo "0"
      ;;
  esac
}

is_true_flag() {
  local raw="${1:-}"
  case "${raw,,}" in
    1|true|yes|on)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

start_virtual_tpm() {
  if ! command -v swtpm >/dev/null 2>&1; then
    echo "swtpm binary not found; continuing without virtual TPM." >&2
    return 1
  fi
  mkdir -p "$TPM_SOCKET_DIR" "$TPM_STATE_DIR"
  rm -f "$TPM_SOCKET_PATH"
  swtpm socket \
    --tpm2 \
    --daemon \
    --ctrl "type=unixio,path=${TPM_SOCKET_PATH}" \
    --tpmstate "dir=${TPM_STATE_DIR}" \
    --log level=20
}

should_auto_boot_iso_keypress() {
  local raw="${AUTO_BOOT_ISO_KEYPRESS,,}"
  case "$raw" in
    1|true|yes|on)
      return 0
      ;;
    0|false|no|off)
      return 1
      ;;
    auto|"")
      if [[ "${OS_TYPE,,}" != "windows" ]]; then
        return 1
      fi
      if [[ -z "$BOOT_ISO" ]]; then
        return 1
      fi
      if [[ "${BOOT_ORDER,,}" != *d* ]]; then
        return 1
      fi
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

send_initial_boot_keypresses() {
  if ! command -v socat >/dev/null 2>&1; then
    echo "socat is unavailable; skipping auto keypress."
    return 0
  fi
  local attempts="${AUTO_BOOT_KEYPRESS_COUNT}"
  local interval="${AUTO_BOOT_KEYPRESS_INTERVAL_SECONDS}"
  if ! [[ "$attempts" =~ ^[0-9]+$ ]] || (( attempts < 1 )); then
    attempts=15
  fi
  if ! [[ "$interval" =~ ^[0-9]+$ ]] || (( interval < 1 )); then
    interval=1
  fi
  local _wait
  for _wait in $(seq 1 30); do
    if [[ -S "$QEMU_MONITOR_SOCKET" ]]; then
      break
    fi
    sleep 1
  done
  if [[ ! -S "$QEMU_MONITOR_SOCKET" ]]; then
    echo "QEMU monitor socket unavailable; skipping auto keypress."
    return 0
  fi
  echo "Auto-sending keypresses to pass ISO boot prompt (${attempts} attempts)."
  local _try
  for _try in $(seq 1 "$attempts"); do
    printf 'sendkey spc\n' | socat - UNIX-CONNECT:"$QEMU_MONITOR_SOCKET" >/dev/null 2>&1 || true
    printf 'sendkey ret\n' | socat - UNIX-CONNECT:"$QEMU_MONITOR_SOCKET" >/dev/null 2>&1 || true
    sleep "$interval"
  done
}

iso_supports_uefi_boot() {
  local iso_path="$1"
  local max_scan_bytes="${UEFI_MARKER_SCAN_MAX_BYTES}"
  python3 - "$iso_path" "$max_scan_bytes" <<'PY'
import sys

path = sys.argv[1]
try:
    max_scan = int(sys.argv[2]) if len(sys.argv) > 2 else 67_108_864
except Exception:
    max_scan = 67_108_864
if max_scan < 1:
    max_scan = 67_108_864
markers = (
    b"BOOTX64.EFI",
    b"BOOTIA32.EFI",
    b"EFI/BOOT",
    b"bootx64.efi",
    b"bootia32.efi",
    b"efi/boot",
)
chunk_size = 4 * 1024 * 1024
tail = b""
scanned = 0
try:
    with open(path, "rb", buffering=0) as handle:
        while scanned < max_scan:
            chunk = handle.read(min(chunk_size, max_scan - scanned))
            if not chunk:
                break
            scanned += len(chunk)
            blob = tail + chunk
            if any(marker in blob for marker in markers):
                print("1")
                raise SystemExit(0)
            tail = blob[-256:]
except Exception:
    pass
print("0")
PY
}

iso_has_el_torito_boot_record() {
  local iso_path="$1"
  local max_scan_sectors="${ISO_BOOT_RECORD_SCAN_MAX_SECTORS}"
  python3 - "$iso_path" "$max_scan_sectors" <<'PY'
import sys

path = sys.argv[1]
try:
    max_sectors = int(sys.argv[2]) if len(sys.argv) > 2 else 512
except Exception:
    max_sectors = 512
if max_sectors < 32:
    max_sectors = 512

sector_size = 2048
start_sector = 16
limit_sector = start_sector + max_sectors
marker = b"EL TORITO SPECIFICATION"

try:
    with open(path, "rb", buffering=0) as handle:
        handle.seek(start_sector * sector_size)
        for _ in range(start_sector, limit_sector):
            block = handle.read(sector_size)
            if len(block) < sector_size:
                break
            if block[1:6] != b"CD001":
                continue
            descriptor_type = block[0]
            if descriptor_type == 0 and marker in block[7:39]:
                print("1")
                raise SystemExit(0)
            if descriptor_type == 255:
                break
except Exception:
    pass
print("0")
PY
}

find_bootable_iso_fallback() {
  local original_iso="$1"
  local iso_dir
  iso_dir="$(dirname "$original_iso")"
  if [[ ! -d "$iso_dir" ]]; then
    return 1
  fi
  local original_base
  original_base="$(basename "$original_iso")"
  local candidate
  local preferred=()
  local others=()
  shopt -s nullglob
  for candidate in "$iso_dir"/*.iso "$iso_dir"/*.ISO; do
    local base
    local base_lc
    base="$(basename "$candidate")"
    base_lc="${base,,}"
    if [[ "$base" == "$original_base" ]]; then
      continue
    fi
    if [[ "${OS_TYPE,,}" == "windows" && "$base_lc" == *win* ]]; then
      preferred+=("$candidate")
    else
      others+=("$candidate")
    fi
  done
  shopt -u nullglob
  local ordered=("${preferred[@]}" "${others[@]}")
  for candidate in "${ordered[@]}"; do
    if [[ "$(iso_has_el_torito_boot_record "$candidate")" == "1" ]] || [[ "$(iso_supports_uefi_boot "$candidate")" == "1" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

# Parse args from API style: --disk <path> --console <url> --cpu N --ram MB
while [[ $# -gt 0 ]]; do
  case "$1" in
    --disk)
      DISK="$2"; shift 2;;
    --console)
      CONSOLE_WS="$2"; shift 2;;
    --cpu)
      CPU_CORES="$2"; shift 2;;
    --ram)
      RAM_MB="$2"; shift 2;;
    *)
      echo "Unknown arg: $1" >&2; shift;;
  esac
done

if [[ -z "$DISK" ]]; then
  echo "Disk path is required via --disk" >&2
  exit 1
fi

if [[ ! -f "$DISK" ]]; then
  echo "Disk not found: $DISK" >&2
  exit 1
fi

DRIVE_IF="${DRIVE_IF:-ide}"
VGA_TYPE="${VGA_TYPE:-qxl}"
CPU_CORES="${CPU_CORES:-2}"
RAM_MB="${RAM_MB:-4096}"
VM_NET_QUEUES="${VM_NET_QUEUES:-${CPU_CORES}}"
CONSOLE_PROVIDER="$(printf '%s' "$CONSOLE_PROVIDER" | tr '[:upper:]' '[:lower:]')"
case "$CONSOLE_PROVIDER" in
  guacamole_rdp|guacamole-rdp|guac-rdp|rdp)
    CONSOLE_PROVIDER="guacamole_rdp"
    ;;
  guacamole|guac|novnc|vnc)
    CONSOLE_PROVIDER="guacamole"
    ;;
  spice|spice-vnc|spice_vnc)
    CONSOLE_PROVIDER="spice"
    ;;
  *)
    CONSOLE_PROVIDER="spice"
    ;;
esac
if ! [[ "$VM_NET_QUEUES" =~ ^[0-9]+$ ]]; then
  VM_NET_QUEUES=1
fi
if (( VM_NET_QUEUES < 1 )); then
  VM_NET_QUEUES=1
fi
if (( VM_NET_QUEUES > 8 )); then
  VM_NET_QUEUES=8
fi
if [[ "${VM_NET_MULTIQUEUE_ENABLED,,}" != "true" ]]; then
  VM_NET_QUEUES=1
fi
if ! [[ "$RDP_FORWARD_PORT" =~ ^[0-9]+$ ]]; then
  RDP_FORWARD_PORT=33890
fi
if (( RDP_FORWARD_PORT < 1024 || RDP_FORWARD_PORT > 65535 )); then
  RDP_FORWARD_PORT=33890
fi
if [[ "$CONSOLE_PROVIDER" == "guacamole_rdp" && "${VM_NET_BACKEND,,}" != "user" ]]; then
  echo "CONSOLE_PROVIDER=guacamole_rdp requires VM_NET_BACKEND=user for deterministic local RDP forwarding" >&2
  exit 1
fi
if [[ "$CONSOLE_PROVIDER" != "spice" && "${VGA_TYPE}" == "qxl" ]]; then
  VGA_TYPE="std"
fi

USE_VIRTUAL_TPM="$(is_enabled_flag "$VM_TPM_ENABLED")"
USE_SECURE_BOOT="$(is_enabled_flag "$VM_SECURE_BOOT")"
if [[ "$USE_SECURE_BOOT" == "1" && "${EFI_ENABLED,,}" != "true" ]]; then
  EFI_ENABLED="true"
  echo "Secure boot requested; enabling EFI firmware."
fi

if [[ -n "$BOOT_ISO" && "${EFI_ENABLED,,}" == "true" ]]; then
  if [[ ! -f "$BOOT_ISO" ]]; then
    echo "Boot ISO not found: $BOOT_ISO" >&2
    exit 1
  fi
  if [[ "$(iso_supports_uefi_boot "$BOOT_ISO")" != "1" ]]; then
    if [[ "${OS_TYPE,,}" == "windows" ]]; then
      echo "UEFI markers were not detected within scan limit; keeping EFI enabled for Windows installer media."
    elif is_true_flag "$UEFI_FALLBACK_TO_BIOS_ON_NO_MARKER"; then
      echo "BOOT_ISO appears BIOS-only; disabling EFI for this launch."
      if [[ "$USE_SECURE_BOOT" == "1" ]]; then
        echo "Windows secure boot requirements will not be met with a BIOS-only ISO." >&2
      fi
      EFI_ENABLED="false"
    else
      echo "UEFI markers were not detected within scan limit; keeping EFI setting unchanged."
    fi
  fi
fi

if [[ -n "$BOOT_ISO" && "${BOOT_ORDER,,}" == *d* ]]; then
  iso_bootable="0"
  if [[ "$(iso_has_el_torito_boot_record "$BOOT_ISO")" == "1" ]]; then
    iso_bootable="1"
  elif [[ "$(iso_supports_uefi_boot "$BOOT_ISO")" == "1" ]]; then
    iso_bootable="1"
  fi
  if [[ "$iso_bootable" != "1" ]]; then
    fallback_boot_iso="$(find_bootable_iso_fallback "$BOOT_ISO" || true)"
    if [[ -n "$fallback_boot_iso" ]]; then
      echo "BOOT_ISO is not bootable; using fallback boot ISO ${fallback_boot_iso} and keeping original attached as secondary CD."
      SECONDARY_ISO="$BOOT_ISO"
      BOOT_ISO="$fallback_boot_iso"
      if [[ "${BOOT_ORDER,,}" != *d* ]]; then
        BOOT_ORDER="d${BOOT_ORDER}"
      fi
      if [[ "${BOOT_ORDER,,}" != *c* ]]; then
        BOOT_ORDER="${BOOT_ORDER}c"
      fi
      BOOT_ORDER="${BOOT_ORDER:0:3}"
    else
      original_boot_order="$BOOT_ORDER"
      BOOT_ORDER="$(printf '%s' "$BOOT_ORDER" | tr -d 'dD')"
      if [[ -z "$BOOT_ORDER" ]]; then
        BOOT_ORDER="c"
      elif [[ "${BOOT_ORDER,,}" != *c* ]]; then
        BOOT_ORDER="c${BOOT_ORDER}"
      fi
      BOOT_ORDER="${BOOT_ORDER:0:3}"
      echo "BOOT_ISO does not appear bootable; falling back from boot order ${original_boot_order} to ${BOOT_ORDER}."
    fi
  fi
fi

# Detect actual disk format from image metadata.
DETECTED_DISK_FORMAT=$(python3 - "$DISK" <<'PY'
import json, subprocess, sys
path = sys.argv[1]
fmt = ""
try:
    out = subprocess.check_output(["qemu-img", "info", "--output=json", path], text=True, stderr=subprocess.DEVNULL)
    fmt = (json.loads(out).get("format") or "").strip()
except Exception:
    pass
if not fmt and path.lower().endswith(".vhd"):
    fmt = "vpc"
print(fmt or "raw")
PY
)

DISK_FORMAT="$DISK_FORMAT_ENV"
if [[ -z "$DISK_FORMAT" ]]; then
  DISK_FORMAT="$DETECTED_DISK_FORMAT"
elif [[ "$DISK_FORMAT" != "$DETECTED_DISK_FORMAT" ]]; then
  echo "DISK_FORMAT=${DISK_FORMAT} does not match detected format ${DETECTED_DISK_FORMAT}; using detected format." >&2
  DISK_FORMAT="$DETECTED_DISK_FORMAT"
fi

# Derive console port from env (WS_PORT). For QEMU VNC websocket we need plain WS; Guac will terminate TLS.
VNC_PORT=$(python3 - <<'PY'
import os
disp = os.environ.get("VNC_DISPLAY", ":0")
if not disp.startswith(":"):
    disp = f":{disp}"
num = int(disp[1:])
print(5900 + num)
PY
)

QEMU_VNC_BIND=$(python3 - <<'PY'
import os
disp = os.environ.get("VNC_DISPLAY", ":0")
if not disp.startswith(":"):
    disp = f":{disp}"
try:
    num = int(disp[1:])
except Exception:
    num = 0
print(f"127.0.0.1:{num}")
PY
)

if [[ "$CONSOLE_PROVIDER" == "guacamole" ]]; then
  WEBROOT="/usr/share/novnc"
elif [[ "$CONSOLE_PROVIDER" == "guacamole_rdp" ]]; then
  WEBROOT="/opt/runner/guac-web"
else
  WEBROOT="/usr/share/spice-html5"
fi
if [[ ! -d "$WEBROOT" ]]; then
  WEBROOT="/usr/share/novnc"
fi
if [[ ! -d "$WEBROOT" ]]; then
  WEBROOT="/opt/runner"
  mkdir -p "$WEBROOT"
fi

SPICE_PORT=${SPICE_PORT:-5930}
CONSOLE_TARGET_PORT="$SPICE_PORT"
if [[ "$CONSOLE_PROVIDER" == "guacamole" ]]; then
  CONSOLE_TARGET_PORT="$VNC_PORT"
fi
if [[ "$CONSOLE_PROVIDER" == "guacamole_rdp" ]]; then
  mkdir -p "$WEBROOT/guacamole"
  cp /opt/runner/rdp.html "$WEBROOT/rdp.html"
  GUAC_JS_SOURCE="/opt/runner/guacamole/all.min.js"
  if [[ ! -f "$GUAC_JS_SOURCE" ]]; then
    echo "Missing guacamole-common-js browser bundle: $GUAC_JS_SOURCE" >&2
    exit 1
  fi
  cp "$GUAC_JS_SOURCE" "$WEBROOT/guacamole/all.min.js"
  if [[ -z "$GUAC_TOKEN_KEY" ]]; then
    GUAC_TOKEN_KEY="$(python3 - <<'PY'
import secrets
alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
print("".join(secrets.choice(alphabet) for _ in range(48)))
PY
)"
  fi
  guacd -b 127.0.0.1 -l 4822 &
  GUACD_HOST="127.0.0.1" \
  GUACD_PORT="4822" \
  GUAC_HTTP_PORT="$WS_PORT" \
  GUAC_WEB_ROOT="$WEBROOT" \
  GUAC_TUNNEL_PATH="/rdp-tunnel" \
  GUAC_TOKEN_KEY="$GUAC_TOKEN_KEY" \
  GUAC_RDP_HOST="127.0.0.1" \
  GUAC_RDP_PORT="$RDP_FORWARD_PORT" \
  GUAC_RDP_SECURITY="$GUAC_RDP_SECURITY" \
  GUAC_RDP_IGNORE_CERT="$GUAC_RDP_IGNORE_CERT" \
  node /opt/runner/guac-rdp-server.js &
else
  WEBSOCKIFY_ARGS=(--web="$WEBROOT")
  if [[ -n "${TLS_CERT_FILE:-}" && -n "${TLS_KEY_FILE:-}" && -f "${TLS_CERT_FILE}" && -f "${TLS_KEY_FILE}" ]]; then
    WEBSOCKIFY_ARGS+=(--cert="$TLS_CERT_FILE" --key="$TLS_KEY_FILE")
  fi
  websockify "${WEBSOCKIFY_ARGS[@]}" "$WS_PORT" "localhost:$CONSOLE_TARGET_PORT" --daemon
fi

if [[ "$CONSOLE_PROVIDER" == "spice" ]]; then
  SPICE_ARGS="port=${SPICE_PORT},addr=0.0.0.0"
  if [[ "${SPICE_TICKETING,,}" == "true" ]]; then
    if [[ -z "$SPICE_PASSWORD" ]]; then
      SPICE_PASSWORD="$(python3 - <<'PY'
import secrets
alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
print("".join(secrets.choice(alphabet) for _ in range(24)))
PY
)"
    fi
    SPICE_ARGS="${SPICE_ARGS},disable-ticketing=off,password=${SPICE_PASSWORD}"
  else
    SPICE_ARGS="${SPICE_ARGS},disable-ticketing=on"
  fi
fi

QEMU_ARGS=(
  -m "${RAM_MB}"
  -smp "${CPU_CORES}"
  -boot "${BOOT_ORDER}"
  -display none
  -device ich9-usb-ehci1
  -device ich9-usb-uhci1
  -device ich9-usb-uhci2
  -device ich9-usb-uhci3
  -device usb-tablet
  -machine accel=kvm:tcg
  -rtc base=localtime
)
if [[ "$CONSOLE_PROVIDER" == "spice" ]]; then
  QEMU_ARGS+=(
    -spice "${SPICE_ARGS}"
    -device virtio-serial
    -chardev spicevmc,id=vdagent,debug=0,name=vdagent
    -device virtserialport,chardev=vdagent,name=com.redhat.spice.0
  )
elif [[ "$CONSOLE_PROVIDER" == "guacamole" ]]; then
  QEMU_ARGS+=(-vnc "${QEMU_VNC_BIND}")
fi

# If KVM is available, add -enable-kvm
if [[ -c /dev/kvm ]]; then
  QEMU_ARGS+=(-enable-kvm)
elif [[ "${CPU_MODEL}" == "host" ]]; then
  # host CPU model requires hardware acceleration; use a safe emulated model when KVM is absent.
  CPU_MODEL="max"
fi

# Optional UEFI pflash.
OVMF_CODE=""
OVMF_VARS_TEMPLATE=""

if [[ "$USE_SECURE_BOOT" == "1" ]]; then
  OVMF_CANDIDATES=(
    "/usr/share/OVMF/OVMF_CODE_4M.ms.fd"
    "/usr/share/OVMF/OVMF_CODE_4M.secboot.fd"
    "/usr/share/OVMF/OVMF_CODE_4M.snakeoil.fd"
    "/usr/share/OVMF/OVMF_CODE_4M.fd"
    "/usr/share/OVMF/OVMF_CODE.fd"
    "/usr/share/edk2/ovmf/OVMF_CODE_4M.fd"
    "/usr/share/edk2/ovmf/OVMF_CODE.fd"
  )
else
  OVMF_CANDIDATES=(
    "/usr/share/OVMF/OVMF_CODE.fd"
    "/usr/share/OVMF/OVMF_CODE_4M.fd"
    "/usr/share/edk2/ovmf/OVMF_CODE.fd"
    "/usr/share/edk2/ovmf/OVMF_CODE_4M.fd"
  )
fi

for candidate_code in "${OVMF_CANDIDATES[@]}"; do
  case "$candidate_code" in
    */OVMF_CODE_4M.ms.fd)
      candidate_vars="${candidate_code%OVMF_CODE_4M.ms.fd}OVMF_VARS_4M.ms.fd"
      ;;
    */OVMF_CODE_4M.secboot.fd)
      candidate_vars="${candidate_code%OVMF_CODE_4M.secboot.fd}OVMF_VARS_4M.fd"
      ;;
    */OVMF_CODE_4M.snakeoil.fd)
      candidate_vars="${candidate_code%OVMF_CODE_4M.snakeoil.fd}OVMF_VARS_4M.snakeoil.fd"
      ;;
    */OVMF_CODE_4M.fd)
      candidate_vars="${candidate_code%OVMF_CODE_4M.fd}OVMF_VARS_4M.fd"
      ;;
    *)
      candidate_vars="${candidate_code%OVMF_CODE.fd}OVMF_VARS.fd"
      ;;
  esac
  if [[ -f "$candidate_code" && -f "$candidate_vars" ]]; then
    OVMF_CODE="$candidate_code"
    OVMF_VARS_TEMPLATE="$candidate_vars"
    break
  fi
done

if [[ "${EFI_ENABLED,,}" == "true" && -n "$OVMF_CODE" && -n "$OVMF_VARS_TEMPLATE" ]]; then
  OVMF_VARS="/tmp/OVMF_VARS.fd"
  cp "$OVMF_VARS_TEMPLATE" "$OVMF_VARS" 2>/dev/null || true
  QEMU_ARGS+=(-drive if=pflash,format=raw,readonly=on,file="$OVMF_CODE")
  QEMU_ARGS+=(-drive if=pflash,format=raw,file="$OVMF_VARS")
fi

if [[ "$USE_VIRTUAL_TPM" == "1" ]]; then
  if start_virtual_tpm; then
    QEMU_ARGS+=(
      -chardev "socket,id=chrtpm,path=${TPM_SOCKET_PATH}"
      -tpmdev "emulator,id=tpm0,chardev=chrtpm"
      -device "tpm-tis,tpmdev=tpm0"
    )
    echo "Virtual TPM 2.0 enabled."
  fi
fi

if should_auto_boot_iso_keypress; then
  ENABLE_AUTO_BOOT_KEYPRESS="1"
  rm -f "$QEMU_MONITOR_SOCKET"
  QEMU_ARGS+=(-monitor "unix:${QEMU_MONITOR_SOCKET},server,nowait")
fi

QEMU_ARGS+=(
  -machine "${MACHINE_TYPE}"
  -cpu "${CPU_MODEL}"
  -vga "${VGA_TYPE}"
  -serial stdio
)

cleanup_net() {
  set +e
  local egress_if="${TAP_EGRESS_IF:-$(detect_egress_interface)}"
  if [[ -f /tmp/dnsmasq.pid ]]; then
    kill "$(cat /tmp/dnsmasq.pid)" >/dev/null 2>&1 || true
    rm -f /tmp/dnsmasq.pid
  fi
  iptables -t nat -D POSTROUTING -s 192.168.241.0/24 -o "${egress_if}" -j MASQUERADE >/dev/null 2>&1 || true
  iptables -D FORWARD -i tap0 -o "${egress_if}" -j ACCEPT >/dev/null 2>&1 || true
  iptables -D FORWARD -i "${egress_if}" -o tap0 -m state --state RELATED,ESTABLISHED -j ACCEPT >/dev/null 2>&1 || true
  ip addr del 192.168.241.1/24 dev tap0 >/dev/null 2>&1 || true
  ip link set tap0 down >/dev/null 2>&1 || true
  ip tuntap del dev tap0 mode tap >/dev/null 2>&1 || true
}

detect_egress_interface() {
  local iface
  iface="$(ip -4 route show default 2>/dev/null | awk '{print $5; exit}')"
  if [[ -z "$iface" ]]; then
    iface="$(ip route show default 2>/dev/null | awk '{print $5; exit}')"
  fi
  echo "${iface:-eth0}"
}

setup_tap_nat() {
  if [[ ! -c /dev/net/tun ]]; then
    echo "tap-nat requested but /dev/net/tun is unavailable; falling back to slirp user networking"
    VM_NET_BACKEND="user"
    return
  fi
  if (( VM_NET_QUEUES > 1 )); then
    if ! ip tuntap add dev tap0 mode tap multi_queue; then
      echo "tap multiqueue setup failed; falling back to single queue networking"
      VM_NET_QUEUES=1
      ip tuntap add dev tap0 mode tap
    fi
  else
    ip tuntap add dev tap0 mode tap
  fi
  ip addr add 192.168.241.1/24 dev tap0
  ip link set tap0 up
  TAP_EGRESS_IF="$(detect_egress_interface)"
  # Forward VM traffic through the pod/node default interface with kernel NAT.
  echo 1 > /proc/sys/net/ipv4/ip_forward
  iptables -t nat -A POSTROUTING -s 192.168.241.0/24 -o "${TAP_EGRESS_IF}" -j MASQUERADE
  iptables -A FORWARD -i tap0 -o "${TAP_EGRESS_IF}" -j ACCEPT
  iptables -A FORWARD -i "${TAP_EGRESS_IF}" -o tap0 -m state --state RELATED,ESTABLISHED -j ACCEPT
  dnsmasq \
    --interface=tap0 \
    --bind-interfaces \
    --except-interface=lo \
    --dhcp-range=192.168.241.50,192.168.241.200,12h \
    --dhcp-option=option:router,192.168.241.1 \
    --dhcp-option=option:dns-server,192.168.241.1 \
    --pid-file=/tmp/dnsmasq.pid
  trap cleanup_net EXIT
}

if [[ "${VM_NET_BACKEND,,}" == "tap-nat" ]]; then
  setup_tap_nat
fi

# Single virtio net device for all OS types.
if [[ "${VM_NET_BACKEND,,}" == "tap-nat" ]]; then
  TAP_NETDEV="tap,id=net0,ifname=tap0,script=no,downscript=no,queues=${VM_NET_QUEUES}"
  if [[ "${VM_VHOST_NET_ENABLED,,}" == "true" ]]; then
    if [[ -c /dev/vhost-net ]]; then
      TAP_NETDEV="${TAP_NETDEV},vhost=on"
    else
      echo "vhost-net requested but /dev/vhost-net is unavailable; continuing without vhost acceleration"
    fi
  fi
  NET_DEVICE="virtio-net-pci,netdev=net0"
  if (( VM_NET_QUEUES > 1 )); then
    NET_DEVICE="${NET_DEVICE},mq=on,vectors=$((2 * VM_NET_QUEUES + 2))"
  fi
  QEMU_ARGS+=(
    -netdev "${TAP_NETDEV}"
    -device "${NET_DEVICE}"
  )
else
  USER_NETDEV="user,id=net0"
  if [[ "$CONSOLE_PROVIDER" == "guacamole_rdp" ]]; then
    USER_NETDEV="${USER_NETDEV},hostfwd=tcp:127.0.0.1:${RDP_FORWARD_PORT}-:3389"
  fi
  NET_DEVICE="virtio-net-pci,netdev=net0"
  if (( VM_NET_QUEUES > 1 )); then
    NET_DEVICE="${NET_DEVICE},mq=on,vectors=$((2 * VM_NET_QUEUES + 2))"
  fi
  QEMU_ARGS+=(
    -netdev "${USER_NETDEV}"
    -device "${NET_DEVICE}"
  )
fi

if [[ "${OS_TYPE,,}" == "linux" ]]; then
  # Single disk path for Linux based on DRIVE_IF (default: sata).
  BUS="${DRIVE_IF:-sata}"
  if [[ "${BUS}" == "virtio" ]]; then
    QEMU_ARGS+=(
      -drive if=none,file="${DISK}",format="${DISK_FORMAT}",id=disk,cache=none
      -device virtio-blk-pci,drive=disk,bootindex=0
    )
  elif [[ "${BUS}" == "ide" ]]; then
    QEMU_ARGS+=(
      -drive "file=${DISK},if=ide,format=${DISK_FORMAT},cache=none"
    )
  else  # sata/default
    QEMU_ARGS+=(
      -drive if=none,file="${DISK}",format="${DISK_FORMAT}",id=disk,cache=none
      -device ich9-ahci,id=ahci
      -device ide-hd,drive=disk,bus=ahci.0,bootindex=0
    )
  fi
  QEMU_ARGS+=(
    -device virtio-mouse-pci
    -device virtio-keyboard-pci
  )
else
  QEMU_ARGS+=(
    -drive "file=${DISK},if=${DRIVE_IF},format=${DISK_FORMAT},cache=none"
  )
fi
if [[ -n "$BOOT_ISO" ]]; then
  QEMU_ARGS+=(
    -drive "file=${BOOT_ISO},media=cdrom,readonly=on"
  )
fi
if [[ -n "$SECONDARY_ISO" ]]; then
  QEMU_ARGS+=(
    -drive "file=${SECONDARY_ISO},media=cdrom,readonly=on"
  )
fi

echo "Starting QEMU with disk=${DISK}, cpu=${CPU_CORES}, ram=${RAM_MB}MB, provider=${CONSOLE_PROVIDER}, ws_port=${WS_PORT}"
echo "Console target: localhost:${CONSOLE_TARGET_PORT}"
if [[ "$CONSOLE_PROVIDER" == "guacamole" ]]; then
  echo "VNC bind address: ${QEMU_VNC_BIND}"
fi
if [[ "$CONSOLE_PROVIDER" == "guacamole_rdp" ]]; then
  echo "RDP forward target: 127.0.0.1:${RDP_FORWARD_PORT} -> guest:3389"
fi
echo "Disk format: ${DISK_FORMAT}"
echo "VM networking: backend=${VM_NET_BACKEND}, queues=${VM_NET_QUEUES}, vhost_net=${VM_VHOST_NET_ENABLED}"
qemu-system-x86_64 "${QEMU_ARGS[@]}" &
QEMU_PID="$!"
if [[ "$ENABLE_AUTO_BOOT_KEYPRESS" == "1" ]]; then
  send_initial_boot_keypresses &
fi
wait "$QEMU_PID"
