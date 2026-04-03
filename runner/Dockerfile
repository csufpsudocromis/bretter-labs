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

FROM ubuntu:24.04
LABEL org.opencontainers.image.source="https://github.com/csufpsudocromis/bretter-labs" \
      org.opencontainers.image.url="https://github.com/csufpsudocromis/bretter-labs" \
      org.opencontainers.image.licenses="MIT"
ARG GUAC_VERSION=1.3.0

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      qemu-system-x86 \
      qemu-utils \
      swtpm \
      websockify \
      novnc \
      spice-html5 \
      guacd \
      ovmf \
      dnsmasq-base \
      iproute2 \
      iptables \
      nodejs \
      npm \
      python3 \
      curl \
      unzip \
      ca-certificates && \
    if apt-cache show libguac-client-rdp0t64 >/dev/null 2>&1; then \
      apt-get install -y --no-install-recommends libguac-client-rdp0t64; \
    elif apt-cache show libguac-client-rdp0 >/dev/null 2>&1; then \
      apt-get install -y --no-install-recommends libguac-client-rdp0; \
    else \
      echo "Missing guacd RDP plugin package in apt repositories" >&2; \
      exit 1; \
    fi && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt/runner

RUN npm install --prefix /opt/runner --omit=dev guacamole-lite@1.2.0

RUN mkdir -p /opt/runner/guacamole && \
    curl -fsSL "https://archive.apache.org/dist/guacamole/${GUAC_VERSION}/binary/guacamole-${GUAC_VERSION}.war" -o /tmp/guacamole.war && \
    unzip -j /tmp/guacamole.war "guacamole-common-js/all.min.js" -d /opt/runner/guacamole && \
    rm -f /tmp/guacamole.war

COPY entrypoint.sh guac-rdp-server.js rdp.html /opt/runner/

ENV WS_PORT=6080 \
    VNC_DISPLAY=":0" \
    CPU_CORES=2 \
    RAM_MB=4096

EXPOSE 6080

ENTRYPOINT ["/opt/runner/entrypoint.sh"]
