#!/usr/bin/env bash
# Optional external EPUB validation. Java and epubcheck are not required by v1.
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <book.epub> [epubcheck args...]" >&2
  exit 2
fi

if ! command -v java >/dev/null 2>&1; then
  echo "epubcheck skipped: java is not installed"
  exit 0
fi

if command -v epubcheck >/dev/null 2>&1; then
  exec epubcheck "$@"
fi

if [ -n "${EPUBCHECK_JAR:-}" ] && [ -f "${EPUBCHECK_JAR}" ]; then
  exec java -jar "${EPUBCHECK_JAR}" "$@"
fi

echo "epubcheck skipped: neither the epubcheck command nor EPUBCHECK_JAR is available"
exit 0
