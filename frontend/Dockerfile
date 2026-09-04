# RecoverAI frontend — Vite build served by nginx, which also reverse-
# proxies /api and /health to the backend so the browser talks to a
# single origin (no CORS in the container setup).

FROM node:22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Baked into the bundle at build time. Points at this same nginx origin;
# nginx.conf proxies /api and /health through to the backend service.
ARG VITE_API_BASE_URL=http://localhost:8080
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
# Must match the backend's API_KEY when auth is enabled. Empty by default.
# This is compiled into the bundle and readable in devtools — a deployment
# gate, not a secret (see docs/security.md).
ARG VITE_API_KEY=
ENV VITE_API_KEY=$VITE_API_KEY
RUN npm run build


FROM nginx:1.27-alpine AS runtime

RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/recoverai.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
