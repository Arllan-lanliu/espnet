#!/usr/bin/env bash

# Set bash to 'debug' mode, it will exit on :
# -e 'error', -u 'undefined variable', -o ... 'error in pipeline', -x 'print commands',
set -e
set -u
set -o pipefail


kmeans_feature="wavlm_large/21"  # use model_type/layer_index
nclusters=1000

src_lang=$(echo "${kmeans_feature}_km${nclusters}" | tr "/" "_")
tgt_lang=en

train_set="train"
train_dev="dev"
test_sets="dev test"

asr_config=conf/tuning/train_discrete_asr_e_branchformer1_e12.yaml
inference_config=conf/decode_ctc0.3.yaml
gpu_inference=true

src_nbpe=6000   # I use src_nbpe=6000 for 2000-cluster kmeans.
tgt_nbpe=3000   # if token_joint is True, then only tgt_nbpe is used

# ts: true sequence
# rm: deduplicated sequence which removes duplicated tokens
src_case="rm"
tgt_case="ts"

./asr2.sh \
    --kmeans_feature "${kmeans_feature}" \
    --kmeans_opts "--portion 0.02 --nj 8" \
    --use_lm false \
    --use_ngram false \
    --nclusters "${nclusters}" \
    --ngpu 4 \
    --gpu_inference "${gpu_inference}" \
    --src_lang ${src_lang} \
    --tgt_lang ${tgt_lang} \
    --src_token_type "bpe" \
    --audio_format "flac.ark" \
    --src_nbpe $src_nbpe \
    --tgt_token_type "bpe" \
    --tgt_nbpe $tgt_nbpe \
    --src_case ${src_case} \
    --tgt_case ${tgt_case} \
    --speed_perturb_factors "" \
    --asr_config "${asr_config}" \
    --inference_config "${inference_config}" \
    --train_set "${train_set}" \
    --valid_set "${train_dev}" \
    --test_sets "${test_sets}" \
    --src_bpe_train_text "data/${train_set}/text.${src_case}.${src_lang}" \
    --tgt_bpe_train_text "data/${train_set}/text.${tgt_case}.${tgt_lang}" \
    --lm_train_text "data/${train_set}/text.${tgt_case}.${tgt_lang} data/local/other_text/text" "$@"
