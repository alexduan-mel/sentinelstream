#!/bin/sh
# Entrypoint for every container started from the app image (api now; scheduler and worker later).
# Applies pending migrations under the runner's advisory lock, then runs the container's command.
set -eu
python -m orchestration.migrations
exec "$@"
