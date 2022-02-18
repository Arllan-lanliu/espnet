#!/bin/bash
# Set bash to 'debug' mode, it will exit on :
# -e 'error', -u 'undefined variable', -o ... 'error in pipeline', -x 'print commands',
set -e
set -u
set -o pipefail

train_set=tr_no_dev
valid_set=dev
test_sets="dev eval1"

token_type=char

asr_config=conf/train_asr_transformer.yaml
inference_config=conf/decode_asr.yaml
lm_config=conf/train_lm_transformer.yaml

# speed perturbation related
# (train_set will be "${train_set}_sp" if speed_perturb_factors is specified)
speed_perturb_factors="1.1 0.9 1.0"

./asr.sh \
    --ngpu 4 \
    --token_type "${token_type}" \
    --feats_type fbank_pitch \
    --train_set "${train_set}" \
    --valid_set "${valid_set}" \
    --test_sets "${test_sets}" \
    --use_lm false \
    --asr_config "${asr_config}" \
    --lm_config "${lm_config}" \
    --inference_config "${inference_config}" \
    --speed_perturb_factors "${speed_perturb_factors}" \
    --lm_train_text "data/${train_set}/text" "$@"
