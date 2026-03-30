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
TAP_EGRESS_IF=""

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
  -boot c
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

for candidate_code in \
  "/usr/share/OVMF/OVMF_CODE.fd" \
  "/usr/share/OVMF/OVMF_CODE_4M.fd" \
  "/usr/share/edk2/ovmf/OVMF_CODE.fd" \
  "/usr/share/edk2/ovmf/OVMF_CODE_4M.fd"; do
  case "$candidate_code" in
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
exec qemu-system-x86_64 "${QEMU_ARGS[@]}"
