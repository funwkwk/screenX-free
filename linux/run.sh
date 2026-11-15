#!/bin/sh
# Starts screenX for Linux from this folder:  linux/run.sh [--snip | --fullscreen | --record | --color | --text]
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m screenx "$@"
