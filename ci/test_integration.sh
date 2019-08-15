#!/usr/bin/env bash

# test asr recipe
(
    cd ./egs/mini_an4/asr1 || exit 1
    ./run.sh
    ./run.sh --stage 3 --backend chainer
)
# test tts recipe
(
    cd ./egs/mini_an4/tts1 || exit 1
    ./run.sh
)

# TODO(karita): test asr_mix, mt, st?
