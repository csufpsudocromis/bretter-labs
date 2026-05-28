# Frontend image for Bretter Labs (Vite build served by nginx)
FROM node:20.19-slim AS build
WORKDIR /app

ARG VITE_API_BASE=/api
ENV VITE_API_BASE=${VITE_API_BASE}

COPY frontend-vite/package*.json ./
RUN npm install

COPY frontend-vite/ ./
RUN npm run build

FROM docker.io/library/nginx:1.27-alpine AS runtime
WORKDIR /usr/share/nginx/html
COPY --from=build /app/dist/ ./
COPY frontend-vite/nginx-entrypoint.sh /usr/local/bin/nginx-entrypoint.sh
RUN chmod +x /usr/local/bin/nginx-entrypoint.sh \
  && chown -R 101:101 /etc/nginx/conf.d /var/cache/nginx /var/run /run /var/log/nginx /usr/share/nginx/html

EXPOSE 8080
EXPOSE 8443
CMD ["/usr/local/bin/nginx-entrypoint.sh"]
