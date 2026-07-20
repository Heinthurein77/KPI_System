FROM node:20-alpine AS build
WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

# Baked into the JS bundle at build time — Vite only exposes VITE_-prefixed vars,
# and only as of the moment `npm run build` runs. Set this as a Railway *build*
# variable pointing at the backend service's public URL.
ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

RUN npm run build

FROM node:20-alpine
WORKDIR /app
RUN npm install -g serve
COPY --from=build /app/dist ./dist

EXPOSE 8080
# -s enables SPA fallback (unmatched routes serve index.html, so React Router's
# client-side routes like /admin/users work on a hard refresh).
CMD ["sh", "-c", "serve -s dist -l ${PORT:-8080}"]
