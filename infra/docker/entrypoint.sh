#!/bin/sh
# Entrypoint for every container started from the app image (api, scheduler, worker).
# Applies pending migrations under the runner's advisory lock (concurrent starts apply each file
# once), then runs the container's command.
set -eu
python -m orchestration.migrations
exec "$@"
