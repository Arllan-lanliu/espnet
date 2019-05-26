#!/usr/bin/env bash

set -euo pipefail

PATH=$(pwd)/bats-core/bin:$(pwd)/shellcheck-stable/bin:$PATH
if ! [ -x "$(command -v bats)" ]; then
    echo "=== install bats ==="
    git clone https://github.com/bats-core/bats-core.git
fi
if ! [ -x "$(command -v shellcheck)" ]; then
    echo "=== install shellcheck ==="
    wget https://storage.googleapis.com/shellcheck/shellcheck-stable.linux.x86_64.tar.xz
    tar -xvf shellcheck-stable.linux.x86_64.tar.xz
fi

echo "=== run shellcheck ==="
find ci utils -name "*.sh" -printf "=> %p\n" -execdir shellcheck -Calways -x -e SC1091 -e SC2086 {} \; | tee -a check_shellcheck
find egs -name "run.sh" -printf "=> %p\n" -execdir shellcheck -Calways -x -e SC1091 -e SC2086 {} \; | tee check_shellcheck
bash -c '! grep -q "SC[0-9]\{4\}" check_shellcheck'

echo "=== run bats ==="
bats test_utils
