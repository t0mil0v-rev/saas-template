#  Сборка статики фронтенда.
#
#  Образ НЕ запускает сервер: он собирает бандл и кладёт его в /dist. В
#  docker-compose сервис `web` копирует /dist в общий том, откуда статику
#  раздаёт Caddy. Так во время работы нет ни Node-процесса, ни nginx -
#  меньше поверхность атаки и меньше памяти.

FROM node:25-bookworm-slim@sha256:81db02c4b671288a03915da9534dbd54f96d0e7c24d80ccc54f5b36b2e684370 AS build
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
# --include=dev обязателен: выше выставлен NODE_ENV=production, при котором
# `npm ci` иначе пропускает devDependencies, а именно там живут tsc и vite,
# которыми собирается бандл (npm run build → `tsc -b && vite build`).
# Без этого сборка падает с `sh: tsc: not found` (exit 127).
RUN npm ci --include=dev

COPY . .
RUN npm run build

# Финальный слой - только собранные файлы. busybox даёт `cp`/`sh` для
# команды публикации в том (см. docker-compose service web).
FROM busybox:1.36@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662 AS dist
COPY --from=build /app/dist /dist
