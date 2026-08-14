#  Сборка статики фронтенда.
#
#  Образ НЕ запускает сервер: он собирает бандл и кладёт его в /dist. В
#  docker-compose сервис `web` копирует /dist в общий том, откуда статику
#  раздаёт Caddy. Так во время работы нет ни Node-процесса, ни nginx -
#  меньше поверхность атаки и меньше памяти.

FROM node:26-bookworm-slim@sha256:cd565714d4da3e84bfd341e31448f81d47c6362198f152345297c9c1154e6341 AS build
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
FROM busybox:1.36@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662 AS dist
COPY --from=build /app/dist /dist
