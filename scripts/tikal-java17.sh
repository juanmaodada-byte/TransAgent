#!/bin/sh
set -eu

: "${JAVA_HOME:?JAVA_HOME is required}"

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
JAVA_BIN="$JAVA_HOME/bin/java"

if [ ! -x "$JAVA_BIN" ]; then
    echo "Java 17 runtime is unavailable." >&2
    exit 1
fi

exec "$JAVA_BIN" \
    -cp "$SCRIPT_DIR/lib/*" \
    net.sf.okapi.applications.tikal.Main \
    "$@"
