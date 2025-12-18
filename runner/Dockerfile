# Simple QEMU-based runner that boots a supplied disk image and exposes a WebSocket-enabled console.
#
# Args:
#   DISK           - absolute path to the disk inside the container (e.g., /images/disk.vhd)
#   WS_PORT        - websocket port for VNC (default 6080)
#   VNC_DISPLAY    - VNC display (:0 => port 5900)
#   RAM_MB         - memory in MB (default 4096)
#   CPU_CORES      - vCPU count (default 2)
#
# Entrypoint expects:
#   --disk <path> --console <ws_base_url>/<owner>/<instance> --cpu <cores> --ram <ram_mb>
#
# The web client can connect to ws://${runner-host}:${WS_PORT} (VNC/SPICE over WebSocket).

FROM debian:12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      qemu-system-x86 \
      qemu-utils \
      websockify \
      novnc \
      spice-html5 \
      ovmf \
      python3 \
      ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt/runner

COPY entrypoint.sh /opt/runner/entrypoint.sh

ENV WS_PORT=6080 \
    VNC_DISPLAY=":0" \
    CPU_CORES=2 \
    RAM_MB=4096

EXPOSE 6080

ENTRYPOINT ["/opt/runner/entrypoint.sh"]
