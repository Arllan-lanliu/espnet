#!/bin/bash

set -e
set -u
set -o pipefail

stage=1
stop_stage=11
nj=16

train_set="train_nodev"
train_dev="train_dev"
eval_set="test_yesno"

asr_config=conf/train_asr.yaml
decode_config=conf/decode.yaml

./asr.sh                                        \
    --stage ${stage}                            \
    --stop_stage ${stop_stage}                  \
    --nj ${nj}                                  \
    --audio_format wav                          \
    --feats_type raw                            \
    --token_type char                           \
    --use_lm false                              \
    --asr_config "${asr_config}"                \
    --decode_config "${decode_config}"          \
    --train_set "${train_set}"                  \
    --dev_set "${train_dev}"                    \
    --eval_sets "${eval_set}"                   \
    --srctexts "data/${train_set}/text" "$@"
