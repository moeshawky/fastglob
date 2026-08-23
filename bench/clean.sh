#!/usr/bin/env bash
# Idempotent cleanup of generated bench artifacts.
set -euo pipefail
cd "$(dirname "$0")"
rm -rf trees results
echo "cleaned: bench/trees bench/results"
