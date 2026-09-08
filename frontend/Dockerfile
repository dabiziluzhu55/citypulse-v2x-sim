FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
# Bind-mount encoded MP4s here in production. Never COPY roadside_media into this image.
RUN mkdir -p /usr/share/nginx/html/roadside-media
EXPOSE 80
