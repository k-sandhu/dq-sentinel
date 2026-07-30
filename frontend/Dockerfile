FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# Unprivileged nginx (#292): same nginx, but it runs as uid 101 and therefore
# listens on 8080 instead of the privileged port 80 (compose maps 3000 -> 8080).
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080

# Static-only probe (no dependency on the API container being up), so an
# unhealthy frontend means nginx itself is down. busybox wget ships in the image.
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
    CMD wget --quiet --spider --tries=1 http://127.0.0.1:8080/ || exit 1
