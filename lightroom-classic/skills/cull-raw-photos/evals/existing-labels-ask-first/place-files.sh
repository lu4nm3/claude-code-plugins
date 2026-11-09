#!/usr/bin/env bash
# Builds shoot/ from the photographer's folders in ~/Pictures (this Mac only).
set -eu
python3 "$(dirname "$0")/../build_fixture.py" existing-labels-ask-first ./shoot
