---
name: dockerize
description: Containerize an application with a clean, minimal, multi-stage Dockerfile and .dockerignore; build and run it. Use when asked to dockerize or ship a container.
version: 1.0.0
metadata:
  category: devops
  tags: [docker, container, build, deploy]
requires:
  bins: [docker]
---

## When to Use
When asked to dockerize, containerize, or ship an app as a container image — for any stack (Node, Python, Go, Java, etc.).

## Procedure
1. Detect the stack: `read_file` on the manifest (package.json, requirements.txt/pyproject.toml, go.mod, pom.xml) to learn language, version, entrypoint, and start command.
2. Identify the runtime port and start command. If unclear, ask rather than guess.
3. `write_file` a `.dockerignore` first (see Quick Reference) to keep build context small and avoid leaking secrets.
4. `write_file` a multi-stage `Dockerfile`: a `build` stage (full toolchain, install deps, compile) and a slim `runtime` stage that copies only the artifacts. Pin a specific base tag (e.g. `node:20-slim`, `python:3.12-slim`), run as a non-root user, and set `EXPOSE` + `CMD`.
5. Build: `bash` → `docker build -t <name>:dev .`
6. Run and smoke-test: `bash` → `docker run --rm -p <host>:<container> <name>:dev`, then curl/hit the port.
7. Report image size (`docker images <name>:dev`) and the run command.

## Quick Reference
```dockerfile
# build stage
FROM node:20-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# runtime stage
FROM node:20-slim
WORKDIR /app
ENV NODE_ENV=production
COPY package*.json ./
RUN npm ci --omit=dev
COPY --from=build /app/dist ./dist
RUN useradd -m app && chown -R app /app
USER app
EXPOSE 3000
CMD ["node", "dist/index.js"]
```
```
# .dockerignore
.git
node_modules
.env*
dist
**/__pycache__
*.log
Dockerfile
.dockerignore
```

## Pitfalls
- Don't COPY the whole context before installing deps — copy the manifest first so dependency layers cache.
- Never bake secrets/.env into the image; pass at runtime via `-e` or `--env-file`.
- Avoid `:latest` base tags (non-reproducible). Use `-slim`/`-alpine`, but verify native deps build on alpine (musl).
- Don't run as root; create and switch to a non-root user.
- Match the base image version to the project's declared runtime version.

## Verification
- `docker build` exits 0.
- `docker run` starts and the app responds on the mapped port (curl returns expected status).
- `docker images` shows a reasonable size (runtime stage, not the build stage).
