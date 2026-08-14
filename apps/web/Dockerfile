#  Сборка статики фронтенда.
#
#  Образ НЕ запускает сервер: он собирает бандл и кладёт его в /dist. В
#  docker-compose сервис `web` копирует /dist в общий том, откуда статику
#  раздаёт Caddy. Так во время работы нет ни Node-процесса, ни nginx -
#  меньше поверхность атаки и меньше памяти.

FROM node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS build
WORKDIR /app

ENV NODE_ENV=production

# Сборочные аргументы прокидываются в бандл на этапе Vite build. Секретов
# здесь быть не должно - всё, что попадёт в VITE_*, окажется в публичном JS.
ARG VITE_PUBLIC_URL=""
ARG VITE_APP_NAME="SaaS Platform"
ENV VITE_PUBLIC_URL=$VITE_PUBLIC_URL \
    VITE_APP_NAME=$VITE_APP_NAME

# Слой зависимостей кэшируется отдельно от исходников.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# Финальный слой - только собранные файлы. busybox даёт `cp`/`sh` для
# команды публикации в том (см. docker-compose service web).
FROM busybox:1.38@sha256:dc2d74b28e4cf8984fa52af1f39bc7c3d9c73760b41a74d629f5d11b1ab28616 AS dist
COPY --from=build /app/dist /dist
