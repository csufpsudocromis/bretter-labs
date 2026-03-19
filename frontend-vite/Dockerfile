# Frontend image for Bretter Labs (Vite build served by nginx)
FROM node:20-slim AS build
WORKDIR /app

ARG VITE_API_BASE=
ENV VITE_API_BASE=${VITE_API_BASE}

COPY frontend-vite/package*.json ./
RUN npm ci

COPY frontend-vite/ ./
RUN npm run build

FROM docker.io/library/nginx:1.27-alpine AS runtime
LABEL org.opencontainers.image.source="https://github.com/csufpsudocromis/bretter-labs" \
      org.opencontainers.image.url="https://github.com/csufpsudocromis/bretter-labs" \
      org.opencontainers.image.licenses="MIT"
WORKDIR /usr/share/nginx/html
COPY --from=build /app/dist/ ./
COPY frontend-vite/nginx-entrypoint.sh /usr/local/bin/nginx-entrypoint.sh
RUN chmod +x /usr/local/bin/nginx-entrypoint.sh \
    && mkdir -p /var/cache/nginx /var/run /etc/nginx/conf.d \
    && chown -R 101:101 /usr/share/nginx/html /var/cache/nginx /var/run /etc/nginx/conf.d /usr/local/bin/nginx-entrypoint.sh

USER 101:101

EXPOSE 8080
EXPOSE 8443
CMD ["/usr/local/bin/nginx-entrypoint.sh"]
