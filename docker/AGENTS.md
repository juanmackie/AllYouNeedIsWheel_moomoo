# Docker DOX

## Purpose

`docker/` owns container support for the Moomoo OpenD sidecar used by the Docker Compose stack.

## Ownership

- `opend/Dockerfile` owns the Ubuntu-based OpenD image, dependency install, OpenD archive download, and binary layout.
- `opend/entrypoint.sh` owns runtime credential loading, OpenD config generation, AppData linking, log handling, and port readiness checks.

## Local Contracts

- Preserve Docker secrets support for Moomoo login and password values.
- Do not bake broker credentials, generated OpenD config, local logs, or database files into images.
- Keep OpenD listening on the expected API port for `connection_docker.json` and `docker-compose.yml`.
- Treat OpenD as broker connectivity only; container changes must not add autonomous trading behavior.
- Be explicit when changing `OPEND_VERSION`, download URLs, exposed ports, or health checks.

## Work Guidance

- Keep shell logic POSIX/Bash compatible for the Ubuntu base image.
- Prefer idempotent startup behavior because the volume may already contain OpenD data from a previous run.
- Keep diagnostic output useful but avoid printing secret values.

## Verification

- For Docker wiring changes, run `docker compose config` when Docker is available.
- For OpenD image changes, run `docker compose build opend` and verify the `opend` service health path when practical.

## Child DOX Index

No child DOX files yet.
