# My Notes — Express + EJS + MongoDB, packaged for the PBL CI/CD pipeline
FROM node:22-alpine

WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000

# Install dependencies first so this layer is cached until package*.json change
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

# Then the application source (.dockerignore keeps node_modules, .env, .git out)
COPY . .

# Don't run as root inside the container
USER node

EXPOSE 3000

# Docker marks the container unhealthy if /health stops returning 200
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "app.js"]
