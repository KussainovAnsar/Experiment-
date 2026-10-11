# LessonLens frontend — Vite dev server image (MVP / demo). For production build `npm run build` and serve
# dist/ with any static server or reverse proxy in front of the API.
FROM node:22-alpine

WORKDIR /app
ENV CI=true

COPY package.json package-lock.json .npmrc ./
RUN npm ci

COPY . .

EXPOSE 5173
# VITE_API_PROXY_TARGET tells vite.config.ts where to proxy /api (docker-compose sets http://api:8000).
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
