#!/bin/sh
set -eu
mkdir -p build
javac -d build StreamProcessor.java StreamWorkload.java
if [ -n "${AUTORESEARCH_ARTIFACT_DIR:-}" ]; then
  java -version > "$AUTORESEARCH_ARTIFACT_DIR/java-version.txt" 2>&1
fi
if [ "${JFR:-0}" = 1 ]; then
  exec java -Xms64m -Xmx256m "-XX:StartFlightRecording=filename=${AUTORESEARCH_ARTIFACT_DIR:-.}/profile.jfr,settings=profile,dumponexit=true,maxsize=32m" -cp build StreamWorkload "$@"
fi
exec java -Xms64m -Xmx256m -cp build StreamWorkload "$@"
