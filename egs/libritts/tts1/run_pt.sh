#!/bin/bash

# Copyright 2020 Nagoya University (Wen-Chin Huang)
#  Apache 2.0  (http://www.apache.org/licenses/LICENSE-2.0)

. ./path.sh || exit 1;
. ./cmd.sh || exit 1;

# general configuration
backend=pytorch
stage=-1
stop_stage=100
ngpu=1       # number of gpu in training
nj=64        # numebr of parallel jobs
dumpdir=dump # directory to dump full features
verbose=1    # verbose option (if set > 1, get more log)
N=0          # number of minibatches to be used (mainly for debugging). "0" uses all minibatches.
seed=1       # random seed number
resume=""    # the snapshot path to resume (if set empty, no effect)

# feature extraction related
fs=24000      # sampling frequency
fmax=""       # maximum frequency
fmin=""       # minimum frequency
n_mels=80     # number of mel basis
n_fft=1024    # number of fft points
n_shift=256   # number of shift points
win_length="" # window length

# config files
train_config=conf/train_pytorch_tacotron2+spkemb.yaml
decode_config=conf/decode.yaml

# decoding related
model=model.loss.best
n_average=1 # if > 0, the model averaged with n_average ckpts will be used instead of model.loss.best
griffin_lim_iters=64  # the number of iterations of Griffin-Lim

# pretrained model related
tts_train_config=
pretrained_decoder_path=
ept_train_config="conf/ept.v1.single.yaml"
ept_decode_config="conf/ae_decode.yaml"
ept_eval=false

# objective evaluation related
asr_model="librispeech.transformer.ngpu4"
outdir=                                 # in case not executed together with decoding & synthesis stage

db_root=downloads


# exp tag
tag="" # tag for managing experiments.
ept_tag="" # tag for managing experiments.

. utils/parse_options.sh || exit 1;

# Set bash to 'debug' mode, it will exit on :
# -e 'error', -u 'undefined variable', -o ... 'error in pipeline', -x 'print commands',
set -e
set -u
set -o pipefail

train_set=train_clean_460
dev_set=dev_clean
eval_set=test_clean
dev_small_set=dev_small
eval_small_set=test_small

feat_tr_dir=${dumpdir}/${train_set}
feat_dt_dir=${dumpdir}/${dev_set}
feat_ev_dir=${dumpdir}/${eval_set}
feat_dtsm_dir=${dumpdir}/${dev_small_set}
feat_evsm_dir=${dumpdir}/${eval_small_set}

dict=data/lang_1char/${train_set}_units.txt
if [ ${stage} -le -1 ] && [ ${stop_stage} -ge -1 ]; then
    echo "stage -1: Small sets preparation"

    utils/copy_data_dir.sh data/${dev_set} data/${dev_small_set}_org
    utils/copy_data_dir.sh data/${eval_set} data/${eval_small_set}_org

    utils/subset_data_dir.sh --first data/${dev_small_set}_org 250 data/${dev_small_set}
    utils/subset_data_dir.sh --first data/${eval_small_set}_org 250 data/${eval_small_set}

    dump.sh --cmd "$train_cmd" --nj ${nj} --do_delta false \
        data/${dev_small_set}/feats.scp data/${train_set}/cmvn.ark exp/dump_feats/dev_small ${feat_dtsm_dir}
    dump.sh --cmd "$train_cmd" --nj ${nj} --do_delta false \
        data/${eval_small_set}/feats.scp data/${train_set}/cmvn.ark exp/dump_feats/eval_small ${feat_evsm_dir}

    echo "Make data json"
    data2json.sh --feat ${feat_dtsm_dir}/feats.scp \
         data/${dev_small_set} ${dict} > ${feat_dtsm_dir}/data.json
    data2json.sh --feat ${feat_evsm_dir}/feats.scp \
         data/${eval_small_set} ${dict} > ${feat_evsm_dir}/data.json
    
    echo "x-vector extraction"
    # Make MFCCs and compute the energy-based VAD for each dataset
    mfccdir=mfcc
    vaddir=mfcc
    for name in ${dev_small_set} ${eval_small_set}; do
        utils/copy_data_dir.sh data/${name} data/${name}_mfcc_16k
        utils/data/resample_data_dir.sh 16000 data/${name}_mfcc_16k
        steps/make_mfcc.sh \
            --write-utt2num-frames true \
            --mfcc-config conf/mfcc.conf \
            --nj 5 --cmd "$train_cmd" \
            data/${name}_mfcc_16k exp/make_mfcc_16k ${mfccdir}
        utils/fix_data_dir.sh data/${name}_mfcc_16k
        sid/compute_vad_decision.sh --nj 4 --cmd "$train_cmd" \
            data/${name}_mfcc_16k exp/make_vad ${vaddir}
        utils/fix_data_dir.sh data/${name}_mfcc_16k
    done

    # Check pretrained model existence
    nnet_dir=exp/xvector_nnet_1a
    if [ ! -e ${nnet_dir} ]; then
        echo "X-vector model does not exist. Download pre-trained model."
        wget http://kaldi-asr.org/models/8/0008_sitw_v2_1a.tar.gz
        tar xvf 0008_sitw_v2_1a.tar.gz
        mv 0008_sitw_v2_1a/exp/xvector_nnet_1a exp
        rm -rf 0008_sitw_v2_1a.tar.gz 0008_sitw_v2_1a
    fi
    # Extract x-vector
    for name in ${dev_small_set} ${eval_small_set}; do
        sid/nnet3/xvector/extract_xvectors.sh --cmd "$train_cmd --mem 4G" --nj 4 \
            ${nnet_dir} data/${name}_mfcc_16k \
            ${nnet_dir}/xvectors_${name}
    done
    # Update json
    for name in ${dev_small_set} ${eval_small_set}; do
        local/update_json.sh ${dumpdir}/${name}/data.json ${nnet_dir}/xvectors_${name}/xvector.scp
    done

    echo "Make data json for encoder training"
    local/make_ae_json.py --input-json ${feat_dtsm_dir}/data.json \
        --output-json ${feat_dtsm_dir}/data.json -O ${feat_dtsm_dir}/ae_data.json
    local/make_ae_json.py --input-json ${feat_evsm_dir}/data.json \
        --output-json ${feat_evsm_dir}/data.json -O ${feat_evsm_dir}/ae_data.json
    
fi

if [ ${stage} -le 0 ] && [ ${stop_stage} -ge 0 ]; then
    echo "stage 0: Data preparation"

    # add speaker label
    local/make_spk_label.py \
        --utt2spk data/${train_set}/utt2spk \
        --spk2utt data/${train_set}/spk2utt \
        data/${train_set}/spk_label
    cat data/${train_set}/spk_label | scp2json.py --key spk_label > data/${train_set}/spk_label.json
        
    ### LEGACY
    # addjson.py --verbose ${verbose} \
    #     ${feat_tr_dir}/data.json data/${train_set}/spk_label.json > ${feat_tr_dir}/data.json
    # mkdir -p ${feat_tr_dir}/.backup
    # echo "json updated. original json is kept in ${feat_tr_dir}/.backup."
    # cp ${feat_tr_dir}/data.json ${feat_tr_dir}/.backup/$(basename ${feat_tr_dir}/data.json)
    # mv ${feat_tr_dir}/data.json.tmp ${feat_tr_dir}/data.json

    # make json for encoder pretraining, using 80-d input and 80-d output
    local/make_ae_json.py --input-json ${feat_tr_dir}/data.json \
        --output-json ${feat_tr_dir}/data.json --spk-label data/${train_set}/spk_label.json -O ${feat_tr_dir}/ae_data.json
    local/make_ae_json.py --input-json ${feat_dt_dir}/data.json \
        --output-json ${feat_dt_dir}/data.json -O ${feat_dt_dir}/ae_data.json
    local/make_ae_json.py --input-json ${feat_ev_dir}/data.json \
        --output-json ${feat_ev_dir}/data.json -O ${feat_ev_dir}/ae_data.json
fi

if [ ${stage} -le 1 ] && [ ${stop_stage} -ge 1 ]; then
    echo "stage 1: Encoder pretraining"

    # Suggested usage:
    # 1. Specify --pretrained_decoder_path (eg. exp/<..>/results/snapshot.ep.xxx)
    # 2. Specify --n_average 0
    # 3. Specify --train_config (original config for TTS) and --ept_train_config (new config for ept)
    # 4. Specfiy --ept_tag

    # check input arguments
    if [ -z ${train_config} ]; then
        echo "Please specify --train_config"
        exit 1
    fi
    if [ -z ${ept_tag} ]; then
        echo "Please specify --ept_tag"
        exit 1
    fi
 
    expname=${train_set}_${backend}_ept_${ept_tag}
    expdir=exp/${expname}
    mkdir -p ${expdir}

    train_config="$(change_yaml.py \
        -a dec-init="${pretrained_decoder_path}" \
        -d model-module \
        -d batch-bins \
        -d accum-grad \
        -d epochs \
        -o "conf/$(basename "${train_config}" .yaml).ept.yaml" "${train_config}")"

    tr_json=${feat_tr_dir}/ae_data.json
    dt_json=${feat_dt_dir}/ae_data.json
    ${cuda_cmd} --gpu ${ngpu} ${expdir}/ept_train.log \
        vc_train.py \
           --backend ${backend} \
           --ngpu ${ngpu} \
           --minibatches ${N} \
           --outdir ${expdir}/ept_results \
           --tensorboard-dir tensorboard/${expname} \
           --verbose ${verbose} \
           --seed ${seed} \
           --resume ${resume} \
           --train-json ${tr_json} \
           --valid-json ${dt_json} \
           --config ${train_config} \
           --config2 ${ept_train_config}
fi

if [ ${stage} -le 2 ] && [ ${stop_stage} -ge 2 ]; then
    echo "stage 2: Encoder pretraining: decoding, synthesis, evaluation"
    
    # Suggested usage:
    # 1. Specify --ept_tag
    # 2. Specify --model

    if [ -z ${ept_tag} ]; then
        echo "Please specify --ept_tag"
        exit 1
    fi
    expdir=exp/${train_set}_${backend}_${ept_tag}
    outdir=${expdir}/ept_outputs_${model}

    pids=() # initialize pids
    for name in ${dev_small_set} ${eval_small_set}; do
    (
        [ ! -e ${outdir}/${name} ] && mkdir -p ${outdir}/${name}
        cp ${dumpdir}/${name}/ae_data.json ${outdir}/${name}
        splitjson.py --parts ${nj} ${outdir}/${name}/ae_data.json
        # decode in parallel
        ${train_cmd} JOB=1:${nj} ${outdir}/${name}/log/decode.JOB.log \
            vc_decode.py \
                --backend ${backend} \
                --ngpu 0 \
                --verbose ${verbose} \
                --out ${outdir}/${name}/feats.JOB \
                --json ${outdir}/${name}/split${nj}utt/ae_data.JOB.json \
                --model ${expdir}/ept_results/${model} \
                --config ${ept_decode_config}
        # concatenate scp files
        for n in $(seq ${nj}); do
            cat "${outdir}/${name}/feats.$n.scp" || exit 1;
        done > ${outdir}/${name}/feats.scp
    ) &
    pids+=($!) # store background pids
    done
    i=0; for pid in "${pids[@]}"; do wait ${pid} || ((i++)); done
    [ ${i} -gt 0 ] && echo "$0: ${i} background jobs are failed." && false

    pids=() # initialize pids
    for name in ${dev_small_set} ${eval_small_set}; do
    (
        [ ! -e ${outdir}_denorm/${name} ] && mkdir -p ${outdir}_denorm/${name}
        apply-cmvn --norm-vars=true --reverse=true data/${train_set}/cmvn.ark \
            scp:${outdir}/${name}/feats.scp \
            ark,scp:${outdir}_denorm/${name}/feats.ark,${outdir}_denorm/${name}/feats.scp
        convert_fbank.sh --nj ${nj} --cmd "${train_cmd}" \
            --fs ${fs} \
            --fmax "${fmax}" \
            --fmin "${fmin}" \
            --n_fft ${n_fft} \
            --n_shift ${n_shift} \
            --win_length "${win_length}" \
            --n_mels ${n_mels} \
            --iters ${griffin_lim_iters} \
            ${outdir}_denorm/${name} \
            ${outdir}_denorm/${name}/log \
            ${outdir}_denorm/${name}/wav

    ) &
    pids+=($!) # store background pids
    done
    i=0; for pid in "${pids[@]}"; do wait ${pid} || ((i++)); done
    [ ${i} -gt 0 ] && echo "$0: ${i} background jobs are failed." && false

fi

if [ ${stage} -le 3 ] && [ ${stop_stage} -ge 3 ]; then
    
    echo "stage 3: Objective Evaluation"
    for name in ${eval_small_set} ${dev_small_set}; do
        local/ob_eval/evaluate.sh --nj ${nj} \
            --do_delta false \
            --db_root ${db_root} \
            --backend pytorch \
            --wer true \
            --api v2 \
            ${asr_model} \
            ${outdir} \
            ${name}
    done

    echo "Finished."
fi

