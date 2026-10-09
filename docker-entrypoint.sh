#!/bin/sh
set -eu

# Existing volumes may contain a database created by a prior root-based image.
chown -R when2gram:when2gram /data
exec su-exec when2gram "$@"
