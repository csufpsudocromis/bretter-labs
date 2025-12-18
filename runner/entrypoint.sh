#!/usr/bin/env bash
set -euo pipefail

DISK=""
CONSOLE_WS=""
DISK_FORMAT_ENV="${DISK_FORMAT:-}"
OS_TYPE="${OS_TYPE:-windows}"
MACHINE_TYPE="${MACHINE_TYPE:-q35}"
EFI_ENABLED="${EFI_ENABLED:-false}"

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

# Detect disk format when not provided. VHDs (vpc) need the right format to boot.
DISK_FORMAT="$DISK_FORMAT_ENV"
# For VHDs, keep the vpc format unless explicitly overridden.
if [[ -z "$DISK_FORMAT" ]]; then
  DISK_FORMAT=$(python3 - "$DISK" <<'PY'
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

# Determine web root for SPICE HTML5 assets if present.
WEBROOT="/usr/share/spice-html5"
if [[ ! -d "$WEBROOT" ]]; then
  WEBROOT="/usr/share/novnc"
fi
if [[ ! -d "$WEBROOT" ]]; then
  WEBROOT="/opt/runner"
  mkdir -p "$WEBROOT"
fi

# Start websockify to wrap SPICE port into websocket for browser SPICE client.
SPICE_PORT=${SPICE_PORT:-5930}
websockify --web="$WEBROOT" "$WS_PORT" "localhost:$SPICE_PORT" --daemon

QEMU_ARGS=(
  -m "${RAM_MB:-4096}"
  -smp "${CPU_CORES:-2}"
  -boot c
  -display none
  -spice "port=${SPICE_PORT},addr=0.0.0.0,disable-ticketing=on"
  -device virtio-serial
  -chardev spicevmc,id=vdagent,debug=0,name=vdagent
  -device virtserialport,chardev=vdagent,name=com.redhat.spice.0
  -device ich9-usb-ehci1
  -device ich9-usb-uhci1
  -device ich9-usb-uhci2
  -device ich9-usb-uhci3
  -device usb-tablet
  -machine accel=kvm:tcg
  -rtc base=localtime
)

# If KVM is available, add -enable-kvm
if [[ -c /dev/kvm ]]; then
  QEMU_ARGS+=(-enable-kvm)
fi

# Optional UEFI pflash.
OVMF_CODE="/usr/share/OVMF/OVMF_CODE.fd"
OVMF_VARS_TEMPLATE="/usr/share/OVMF/OVMF_VARS.fd"
if [[ "${EFI_ENABLED,,}" == "true" && -f "$OVMF_CODE" && -f "$OVMF_VARS_TEMPLATE" ]]; then
  OVMF_VARS="/tmp/OVMF_VARS.fd"
  cp "$OVMF_VARS_TEMPLATE" "$OVMF_VARS" 2>/dev/null || true
  QEMU_ARGS+=(-drive if=pflash,format=raw,readonly=on,file="$OVMF_CODE")
  QEMU_ARGS+=(-drive if=pflash,format=raw,file="$OVMF_VARS")
fi

QEMU_ARGS+=(
  -machine "${MACHINE_TYPE}"
  -cpu host
  -vga "${VGA_TYPE}"
  -serial stdio
)

# Single virtio net device for all OS types.
QEMU_ARGS+=(
  -netdev user,id=net0
  -device virtio-net-pci,netdev=net0
)

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

echo "Starting QEMU with disk=${DISK}, cpu=${CPU_CORES:-2}, ram=${RAM_MB:-4096}MB, vnc=${VNC_DISPLAY:-:0}, ws_port=${WS_PORT}"
echo "Disk format: ${DISK_FORMAT}"
exec qemu-system-x86_64 "${QEMU_ARGS[@]}"
