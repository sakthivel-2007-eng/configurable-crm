# syntax=docker/dockerfile:1

# Local development image. Production build/serve lands in M11.
FROM node:22-alpine AS base

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH
RUN corepack enable

WORKDIR /app


FROM base AS dev

# pnpm-workspace.yaml carries the `allowBuilds` setting. Without it in the build
# context, pnpm refuses to run esbuild's install script and fails the build with
# ERR_PNPM_IGNORED_BUILDS — so it must be copied alongside the manifest, not with
# the rest of the source below.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --frozen-lockfile

COPY . .

EXPOSE 5173
CMD ["pnpm", "dev"]
