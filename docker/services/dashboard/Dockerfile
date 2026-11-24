# syntax=docker/dockerfile:1.7
# MR2 dashboard: Vite build, served by nginx with the repo's template.
FROM node:24.15.0-bookworm-slim AS build
WORKDIR /app
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

FROM nginx:1.27-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends openssl \
 && rm -rf /var/lib/apt/lists/* && mkdir -p /var/www/mr2-tiles
COPY --from=build /app/dist /var/www/mr2-dashboard
COPY scripts/nginx/mr2-dashboard-locations.conf /etc/nginx/snippets/
COPY scripts/nginx/mr2-dashboard.conf.template /etc/mr2/
COPY --from=tooling --chmod=0755 services/dashboard/90-mr2.sh \
     /docker-entrypoint.d/90-mr2.sh
