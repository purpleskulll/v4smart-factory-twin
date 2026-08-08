# ============================================================================
# dashboard-ui — statischer Build, ausgeliefert von nginx
# ============================================================================
FROM node:22-alpine AS build
WORKDIR /src

# Dependency-Layer zuerst (bleibt gecacht, solange sich die Manifeste nicht
# ändern). `npm ci` statt `install`: baut exakt gegen package-lock.json und ist
# damit reproduzierbar.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY tsconfig.json vite.config.ts tailwind.config.js postcss.config.js index.html ./
COPY src ./src
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 80
