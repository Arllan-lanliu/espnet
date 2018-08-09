#!/bin/bash

# Copyright 2018 Kyoto University (Hirofumi Inaguma)
#  Apache 2.0  (http://www.apache.org/licenses/LICENSE-2.0)

. ./path.sh
. ./cmd.sh

# general configuration
backend=pytorch
stage=2       # start from -1 if you need to start from data download
gpu=            # will be deprecated, please use ngpu
ngpu=0          # number of gpus ("0" uses cpu, otherwise use gpu)
debugmode=1
dumpdir=dump   # directory to dump full features
N=0            # number of minibatches to be used (mainly for debugging). "0" uses all minibatches.
verbose=0      # verbose option
resume=        # Resume the training from snapshot

# feature configuration
do_delta=false # true when using CNN

# network archtecture
# encoder related
etype=blstmp     # encoder architecture type
elayers=6
eunits=320
eprojs=320
subsample=1_2_2_1_1 # skip every n frame from input to nth layers
# decoder related
dlayers=1
dunits=300
# attention related
atype=location
aconv_chans=10
aconv_filts=100

# hybrid CTC/attention
mtlalpha=0.5

# minibatch related
batchsize=50
maxlen_in=800  # if input length  > maxlen_in, batchsize is automatically reduced
maxlen_out=150 # if output length > maxlen_out, batchsize is automatically reduced

# optimization related
opt=adadelta
epochs=15

# rnnlm related
lm_weight=0.3

# decoding parameter
beam_size=20
penalty=0.0
maxlenratio=0.0
minlenratio=0.0
ctc_weight=0.3
recog_model=acc.best # set a model to be used for decoding: 'acc.best' or 'loss.best'

# Set this to somewhere where you want to put your data, or where
# someone else has already put it.  You'll want to change this
# if you're not on the CLSP grid.
datadir=/export/corpora4/IWSLT/iwslt-corpus

# exp tag
tag="" # tag for managing experiments.

. utils/parse_options.sh || exit 1;

. ./path.sh
. ./cmd.sh

# check gpu option usage
if [ ! -z $gpu ]; then
    echo "WARNING: --gpu option will be deprecated."
    echo "WARNING: please use --ngpu option."
    if [ $gpu -eq -1 ]; then
        ngpu=0
    else
        ngpu=1
    fi
fi

# Set bash to 'debug' mode, it will exit on :
# -e 'error', -u 'undefined variable', -o ... 'error in pipeline', -x 'print commands',
set -e
set -u
set -o pipefail

train_set=train
train_dev=dev2010
recog_set="offlimit2018 tst2010 tst2011 tst2012 tst2013 tst2014 tst2015"

# if [ ${stage} -le -1 ]; then
#     echo "stage -1: Data Download"
#       local/download_and_untar.sh ${datadir} ${data_url} ${part}
# fi

if [ ${stage} -le 0 ]; then
    ### Task dependent. You have to make data the following preparation part by yourself.
    ### But you can utilize Kaldi recipes in most cases
    echo "stage 0: Data preparation"
    for part in train dev2010 offlimit2018 tst2010 tst2011 tst2012 tst2013 tst2014 tst2015; do
        local/data_prep.sh ${datadir} data/local/${part}
    done
fi

feat_tr_dir=${dumpdir}/${train_set}/delta${do_delta}; mkdir -p ${feat_tr_dir}
feat_dt_dir=${dumpdir}/${train_dev}/delta${do_delta}; mkdir -p ${feat_dt_dir}
if [ ${stage} -le 1 ]; then
    ### Task dependent. You have to design training and dev sets by yourself.
    ### But you can utilize Kaldi recipes in most cases
    echo "stage 1: Feature Generation"
    fbankdir=fbank
    # Generate the fbank features; by default 80-dimensional fbanks with pitch on each frame
    for x in train dev2010 offlimit2018 tst2010 tst2011 tst2012 tst2013 tst2014 tst2015; do
        steps/make_fbank_pitch.sh --cmd "$train_cmd" --nj 20 data/${x} exp/make_fbank/${x} ${fbankdir}
    done

    cp -rf data/${train_set} data/${train_set}_org
    cp -rf data/${train_dev} data/${train_dev}_org

    # remove utt having more than 3000 frames
    # remove utt having more than 400 characters
    remove_longshortdata.sh --maxframes 3000 --maxchars 400 data/${train_set}_org data/${train_set}
    remove_longshortdata.sh --maxframes 3000 --maxchars 400 data/${train_dev}_org data/${train_dev}
    rm -rf data/${train_set}_org
    rm -rf data/${train_dev}_org

    # compute global CMVN
    compute-cmvn-stats scp:data/${train_set}/feats.scp data/${train_set}/cmvn.ark

    # dump features for training
    if [[ $(hostname -f) == *.clsp.jhu.edu ]] && [ ! -d ${feat_tr_dir}/storage ]; then
    utils/create_split_dir.pl \
        /export/b{14,15,16,17}/${USER}/espnet-data/egs/iwslt18st/asr1/dump/${train_set}/delta${do_delta}/storage \
        ${feat_tr_dir}/storage
    fi
    if [[ $(hostname -f) == *.clsp.jhu.edu ]] && [ ! -d ${feat_dt_dir}/storage ]; then
    utils/create_split_dir.pl \
        /export/b{14,15,16,17}/${USER}/espnet-data/egs/iwslt18st/asr1/dump/${train_dev}/delta${do_delta}/storage \
        ${feat_dt_dir}/storage
    fi
    dump.sh --cmd "$train_cmd" --nj 80 --do_delta $do_delta \
        data/${train_set}/feats.scp data/${train_set}/cmvn.ark exp/dump_feats/train ${feat_tr_dir}
    dump.sh --cmd "$train_cmd" --nj 32 --do_delta $do_delta \
        data/${train_dev}/feats.scp data/${train_set}/cmvn.ark exp/dump_feats/dev ${feat_dt_dir}
    for rtask in ${recog_set}; do
        feat_recog_dir=${dumpdir}/${rtask}/delta${do_delta}; mkdir -p ${feat_recog_dir}
        dump.sh --cmd "$train_cmd" --nj 32 --do_delta $do_delta \
            data/${rtask}/feats.scp data/${train_set}/cmvn.ark exp/dump_feats/recog/${rtask} \
            ${feat_recog_dir}
    done
fi

# bpemode (unigram or bpe)
nbpe_en_small=300
nbpe_en_large=10000
nbpe_de=10000
bpemode=unigram

# En
dict_char_en=data/lang_1char/${train_set}_units_en.txt
dict_bpe_en_small=data/lang_1char/${train_set}_${bpemode}${nbpe_en_small}_char_units_en.txt
dict_bpe_en_large=data/lang_1char/${train_set}_${bpemode}${nbpe_en_large}_char_units_en.txt
bpemodel_en_small=data/lang_1char/${train_set}_${bpemode}${nbpe_de}_en_small
bpemodel_en_large=data/lang_1char/${train_set}_${bpemode}${nbpe_de}_en_large
# De
dict_bpe_de=data/lang_1char/${train_set}_${bpemode}${nbpe_de}_units_de.txt
bpemodel_de=data/lang_1char/${train_set}_${bpemode}${nbpe_de}_de
echo "dictionary (English, character): ${dict_char_en}"
echo "dictionary (English, BPE, small): ${dict_bpe_en_small}"
echo "dictionary (English, BPE, large): ${dict_bpe_en_large}"
echo "dictionary (Germany, BPE): ${dict_bpe_de}"
if [ ${stage} -le 2 ]; then
    ### Task dependent. You have to check non-linguistic symbols used in the corpus.
    echo "stage 2: Dictionary and Json Data Preparation"
    mkdir -p data/lang_1char/
    # En, char
    echo "<unk> 1" > ${dict_char_en} # <unk> must be 1, 0 will be used for "blank" in CTC
    text2token.py -s 1 -n 1 data/${train_set}/text | cut -f 2- -d" " | tr " " "\n" \
    | sort | uniq | grep -v -e '^\s*$' | awk '{print $0 " " NR+1}' >> ${dict_char_en}
    wc -l ${dict_char_en}

    # En, BPE (300)
    echo "<unk> 1" > ${dict_bpe_en_small} # <unk> must be 1, 0 will be used for "blank" in CTC
    cut -f 2- -d" " data/${train_set}/text > data/lang_1char/input.txt
    spm_train --input=data/lang_1char/input.txt --vocab_size=${nbpe_en_small} --model_type=${bpemode} --model_prefix=${bpemodel_en_small} --input_sentence_size=100000000
    spm_encode --model=${bpemodel_en_small}.model --output_format=piece < data/lang_1char/input.txt | tr ' ' '\n' | sort | uniq | awk '{print $0 " " NR+1}' >> ${dict_bpe_en_small}
    wc -l ${dict_bpe_en_small}

    # En, BPE (10000)
    echo "<unk> 1" > ${dict_bpe_en_large} # <unk> must be 1, 0 will be used for "blank" in CTC
    cut -f 2- -d" " data/${train_set}/text > data/lang_1char/input.txt
    spm_train --input=data/lang_1char/input.txt --vocab_size=${nbpe_en_large} --model_type=${bpemode} --model_prefix=${bpemodel_en_large} --input_sentence_size=100000000
    spm_encode --model=${bpemodel_en_large}.model --output_format=piece < data/lang_1char/input.txt | tr ' ' '\n' | sort | uniq | awk '{print $0 " " NR+1}' >> ${dict_bpe_en_large}
    wc -l ${dict_bpe_en_large}

    # De, BPE (10000)
    echo "<unk> 1" > ${dict_bpe_de} # <unk> must be 1, 0 will be used for "blank" in CTC
    cut -f 2- -d" " data/${train_set}/text > data/lang_1char/input.txt
    spm_train --input=data/lang_1char/input.txt --vocab_size=${nbpe_de} --model_type=${bpemode} --model_prefix=${bpemodel_de} --input_sentence_size=100000000
    spm_encode --model=${bpemodel_de}.model --output_format=piece < data/lang_1char/input.txt | tr ' ' '\n' | sort | uniq | awk '{print $0 " " NR+1}' >> ${dict_bpe_de}
    wc -l ${dict_bpe_de}

    # make json labels
    data2json.sh --feat ${feat_tr_dir}/feats.scp \
         data/${train_set} ${dict_char_en} > ${feat_tr_dir}/data.json
    data2json.sh --feat ${feat_dt_dir}/feats.scp \
         data/${train_dev} ${dict_char_en} > ${feat_dt_dir}/data.json
    # TODO(Hirofumi): DEのtrans，マルチタスク拡張
    # TODO(hiforumi): 記号の処理

    exit 1

    
    # Update json
    for name in ${train_set} ${train_dev} ${recog_set}; do
        local/update_json.sh ${dumpdir}/${name}/data.json ${nnet_dir}/xvectors_${name}/xvector.scp
    done
fi

exit 1

# # You can skip this and remove --rnnlm option in the recognition (stage 5)
# lmexpdir=exp/train_rnnlm_2layer_bs256
# mkdir -p ${lmexpdir}
# if [ ${stage} -le 3 ]; then
#     echo "stage 3: LM Preparation"
#     lmdatadir=data/local/lm_train
#     mkdir -p ${lmdatadir}
#     text2token.py -s 1 -n 1 data/${train_set}/text | cut -f 2- -d" " | perl -pe 's/\n/ <eos> /g' \
#         > ${lmdatadir}/train.txt
#     text2token.py -s 1 -n 1 data/${train_dev}/text | cut -f 2- -d" " | perl -pe 's/\n/ <eos> /g' \
#         > ${lmdatadir}/valid.txt
#     # use only 1 gpu
#     if [ ${ngpu} -gt 1 ]; then
#         echo "LM training does not support multi-gpu. signle gpu will be used."
#     fi
#     ${cuda_cmd} ${lmexpdir}/train.log \
#         lm_train.py \
#         --ngpu ${ngpu} \
#         --backend ${backend} \
#         --verbose 1 \
#         --outdir ${lmexpdir} \
#         --train-label ${lmdatadir}/train.txt \
#         --valid-label ${lmdatadir}/valid.txt \
#         --epoch 60 \
#         --batchsize 256 \
#         --dict_char_en ${dict_char_en}
# fi
#
# if [ -z ${tag} ]; then
#     expdir=exp/${train_set}_${etype}_e${elayers}_subsample${subsample}_unit${eunits}_proj${eprojs}_d${dlayers}_unit${dunits}_${atype}_aconvc${aconv_chans}_aconvf${aconv_filts}_mtlalpha${mtlalpha}_${opt}_bs${batchsize}_mli${maxlen_in}_mlo${maxlen_out}
#     if ${do_delta}; then
#         expdir=${expdir}_delta
#     fi
# else
#     expdir=exp/${train_set}_${tag}
# fi
# mkdir -p ${expdir}
#
# if [ ${stage} -le 4 ]; then
#     echo "stage 4: Network Training"
#     ${cuda_cmd} --gpu ${ngpu} ${expdir}/train.log \
#         asr_train.py \
#         --ngpu ${ngpu} \
#         --backend ${backend} \
#         --outdir ${expdir}/results \
#         --debugmode ${debugmode} \
#         --dict_char_en ${dict_char_en} \
#         --debugdir ${expdir} \
#         --minibatches ${N} \
#         --verbose ${verbose} \
#         --resume ${resume} \
#         --train-json ${feat_tr_dir}/data.json \
#         --valid-json ${feat_dt_dir}/data.json \
#         --etype ${etype} \
#         --elayers ${elayers} \
#         --eunits ${eunits} \
#         --eprojs ${eprojs} \
#         --subsample ${subsample} \
#         --dlayers ${dlayers} \
#         --dunits ${dunits} \
#         --atype ${atype} \
#         --aconv-chans ${aconv_chans} \
#         --aconv-filts ${aconv_filts} \
#         --mtlalpha ${mtlalpha} \
#         --batch-size ${batchsize} \
#         --maxlen-in ${maxlen_in} \
#         --maxlen-out ${maxlen_out} \
#         --opt ${opt} \
#         --epochs ${epochs}
# fi
#
# if [ ${stage} -le 5 ]; then
#     echo "stage 5: Decoding"
#     nj=32
#
#     for rtask in ${recog_set}; do
#     (
#         decode_dir=decode_${rtask}_beam${beam_size}_e${recog_model}_p${penalty}_len${minlenratio}-${maxlenratio}_ctcw${ctc_weight}_rnnlm${lm_weight}
#         feat_recog_dir=${dumpdir}/${rtask}/delta${do_delta}
#
#         # split data
#         data=data/${rtask}
#         split_data.sh --per-utt ${data} ${nj};
#         sdata=${data}/split${nj}utt;
#
#         # make json labels for recognition
#         for j in `seq 1 ${nj}`; do
#             data2json.sh --feat ${feat_recog_dir}/feats.scp \
#                 ${sdata}/${j} ${dict_char_en} > ${sdata}/${j}/data.json
#         done
#
#         #### use CPU for decoding
#         ngpu=0
#
#         ${decode_cmd} JOB=1:${nj} ${expdir}/${decode_dir}/log/decode.JOB.log \
#             asr_recog.py \
#             --ngpu ${ngpu} \
#             --backend ${backend} \
#             --recog-json ${sdata}/JOB/data.json \
#             --result-label ${expdir}/${decode_dir}/data.JOB.json \
#             --model ${expdir}/results/model.${recog_model}  \
#             --model-conf ${expdir}/results/model.conf  \
#             --beam-size ${beam_size} \
#             --penalty ${penalty} \
#             --maxlenratio ${maxlenratio} \
#             --minlenratio ${minlenratio} \
#             --ctc-weight ${ctc_weight} \
#             --rnnlm ${lmexpdir}/rnnlm.model.best \
#             --lm-weight ${lm_weight} \
#             &
#         wait
#
#         score_sclite.sh --wer true ${expdir}/${decode_dir} ${dict_char_en}
#
#     ) &
#     done
#     wait
#     echo "Finished"
# fi
