# ── VESPER Matter Bridge Docker Image ──────────────────────────────────────
# Runs the matter.js bridge that exposes VESPER simulated devices as
# real Matter endpoints, discoverable by python-matter-server and HA.
FROM node:20-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY package.json ./
RUN npm install --production

# Copy bridge code
COPY bridge.mjs ./

# REST API port
EXPOSE 8484
# Matter mDNS + operational port
EXPOSE 5540/tcp
EXPOSE 5540/udp

# Storage for Matter state
VOLUME ["/app/.matter"]

ENV MATTER_STORAGE_PATH=/app/.matter
ENV NODE_ENV=production

CMD ["node", "bridge.mjs"]
