# Frontend image for Bretter Labs (Vite build served by nginx)
FROM node:20-slim AS build
WORKDIR /app

ARG VITE_API_BASE=http://10.68.48.105:30080
ENV VITE_API_BASE=${VITE_API_BASE}

COPY frontend-vite/package*.json ./
RUN npm install

COPY frontend-vite/ ./
RUN npm run build

FROM docker.io/library/nginx:1.27-alpine AS runtime
WORKDIR /usr/share/nginx/html
COPY --from=build /app/dist/ ./

# SPA fallback so client-side routing works
RUN printf 'server {\n  listen 80;\n  server_name _;\n  root /usr/share/nginx/html;\n  location / {\n    try_files $uri /index.html;\n  }\n}\n' > /etc/nginx/conf.d/default.conf

EXPOSE 80
