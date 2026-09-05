# Frontend: Vite/React dashboard, built static and served via nginx.
# [UNVERIFIED end-to-end] -- same Docker-daemon-unavailable constraint
# as the backend Dockerfile. The dev server (npm run dev) has been
# verified live repeatedly in this repo's build (Phase 12); this
# production build path has not been run through Docker itself.

FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
