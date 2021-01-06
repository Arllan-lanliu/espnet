#!/bin/bash

# Copyright 2020 Johns Hopkins University (Jiatong Shi)
#  Apache 2.0  (http://www.apache.org/licenses/LICENSE-2.0)

. ./path.sh || exit 1;
. ./cmd.sh || exit 1;
. ./db.sh || exit 1;

# general configuration
stage=0       # start from 0 if you need to start from data preparation
stop_stage=100
SECONDS=0
langs="en de fr cy br cv ky ga-IE sl cnh et mn sah dv sv-SE id ar ta ia lv ja rm-sursilv hsb ro fy-NL el rm-vallader as mt ka or vi pa-IN tt kab ca zh-TW it fa eu es ru tr nl eo zh-CN rw pt zh-HK cs pl uk"
lid=true
nlsyms_txt=data/local/nlsyms.txt


log() {
    local fname=${BASH_SOURCE[1]##*/}
    echo -e "$(date '+%Y-%m-%dT%H:%M:%S') (${fname}:${BASH_LINENO[0]}:${FUNCNAME[1]}) $*"
}


# Set bash to 'debug' mode, it will exit on :
# -e 'error', -u 'undefined variable', -o ... 'error in pipeline', -x 'print commands',
set -e
set -u
set -o pipefail

. utils/parse_options.sh

langs=$(echo "${langs}" | tr _ " ")
voxforge_lang="de en es fr it nl pt ru"

train_set=train_li52_lid
train_dev=dev_li52_lid
test_set=

log "data preparation started"

mkdir -p ${COMMONVOICE}
mkdir -p ${VOXFORGE}

for lang in ${langs}; do

    if [ ${stage} -le 0 ] && [ ${stop_stage} -ge 0 ]; then
        log "sub-stage 0: Download Data to ${COMMONVOICE}"

        # base url for downloads.
        # Deprecated url:https://voice-prod-bundler-ee1969a6ce8178826482b88e843c335139bd3fb4.s3.amazonaws.com/cv-corpus-3/$lang.tar.gz
        data_url=https://voice-prod-bundler-ee1969a6ce8178826482b88e843c335139bd3fb4.s3.amazonaws.com/cv-corpus-5.1-2020-06-22/${lang}.tar.gz

        local/download_and_untar.sh ${COMMONVOICE} ${data_url} ${lang}.tar.gz
        rm -f ${COMMONVOICE}/${lang}.tar.gz
    fi

    train_subset=train_"$(echo "${lang}" | tr - _)"_commonvoice
    train_subdev=dev_"$(echo "${lang}" | tr - _)"_commonvoice
    test_subset=test_"$(echo "${lang}" | tr - _)"_commonvoice

    if [ ${stage} -le 1 ] && [ ${stop_stage} -ge 1 ]; then
        log "sub-stage 1: Preparing Data for Commonvoice"

        for part in "validated" "test" "dev"; do
            # use underscore-separated names in data directories.
            local/data_prep.pl "${COMMONVOICE}/cv-corpus-5.1-2020-06-22/${lang}" ${part} data/"$(echo "${part}_${lang}_commonvoice" | tr - _)" "${lang}_commonvoice"
        done

        # remove test&dev data from validated sentences
        utils/copy_data_dir.sh data/"$(echo "validated_${lang}_commonvoice" | tr - _)" data/${train_subset}
        utils/filter_scp.pl --exclude data/${train_subdev}/wav.scp data/${train_subset}/wav.scp > data/${train_subset}/temp_wav.scp
        utils/filter_scp.pl --exclude data/${test_subset}/wav.scp data/${train_subset}/temp_wav.scp > data/${train_subset}/wav.scp
        utils/fix_data_dir.sh data/${train_subset}
    fi
    test_set="${test_set} ${test_subset}"


    if [[ "${voxforge_lang}" == *"${lang}"* ]]; then
        if [ ${stage} -le 0 ] && [ ${stop_stage} -ge 0 ]; then
            log "sub-stage0: Download data to ${VOXFORGE}"

            if [ ! -e "${VOXFORGE}/${lang}/extracted" ]; then
                log "sub-stage 1: Download data to ${VOXFORGE}"
                local/getdata.sh "${lang}" "${VOXFORGE}"
            else
                log "sub-stage 1: ${VOXFORGE}/${lang}/extracted is already existing. Skip data downloading"
            fi
        fi

        if [ ${stage} -le 1 ] && [ ${stop_stage} -ge 1 ]; then
            log "sub-stage 1: Data Preparation for Voxforge"
            selected=${VOXFORGE}/${lang}/extracted
            # Initial normalization of the data
            local/voxforge_data_prep.sh --flac2wav false "${selected}" "${lang}"
            local/voxforge_format_data.sh "${lang}"
	    utils/copy_data_dir.sh --utt-suffix -${lang}_voxforge data/all_"${lang}" data/validated_"${lang}"_voxforge
	    rm -r data/all_${lang}
            # following split consider prompt duplication (but does not consider speaker overlap instead)
            local/split_tr_dt_et.sh data/validated_"${lang}"_voxforge data/train_"${lang}"_voxforge data/dev_"${lang}"_voxforge data/test_"${lang}"_voxforge
        fi

        test_set="${test_set} test_${lang}_voxforge"

    fi

done

log "Using test sets: ${test_set}"

if [ ${stage} -le 2 ] && [ ${stop_stage} -ge 2 ]; then
    log "stage 2: Combine Datadir"

    utils/combine_data.sh --skip_fix true data/train_temp data/train_*
    utils/combine_data.sh --skip_fix true data/dev_temp data/dev_*

    for x in data/train_temp data/dev_temp; do
        cp ${x}/text ${x}/text.org
        paste -d " " \
              <(cut -f 1 -d" " ${x}/text.org) \
              <(cut -f 2- -d" " ${x}/text.org | python3 -c 'import sys; print(sys.stdin.read().upper(), end="")') \
              > ${x}/text
        rm ${x}/text.org
    done

fi

if [ ${stage} -le 3 ] && [ ${stop_stage} -ge 3 ]; then
    log "stage 3: Add Language ID"

    cp -r data/train_temp data/${train_set}
    cp -r data/dev_temp data/${train_dev}

    if [ "$lid" = true ]
    then
        paste -d " " \
      <(cut -f 1 -d" " data/train_temp/text) \
      <(cut -f 1 -d" " data/train_temp/text | sed -e "s/.*\-\(.*\)_.*/\1/" | sed -e "s/_[^TW]\+//" | sed -e "s/^/\[/" -e "s/$/\]/") \
      <(cut -f 2- -d" " data/train_temp/text) | sed -e "s/\([^[]*\[[^]]*\]\)\s\(.*\)/\1\2/" \
      > data/${train_set}/text
        paste -d " " \
      <(cut -f 1 -d" " data/dev_temp/text) \
      <(cut -f 1 -d" " data/dev_temp/text | sed -e "s/.*\-\(.*\)_.*/\1/" | sed -e "s/_[^TW]\+//" | sed -e "s/^/\[/" -e "s/$/\]/") \
      <(cut -f 2- -d" " data/dev_temp/text) | sed -e "s/\([^[]*\[[^]]*\]\)\s\(.*\)/\1\2/" \
      > data/${train_dev}/text
    fi

    utils/fix_data_dir.sh data/${train_set}
    utils/fix_data_dir.sh data/${train_dev}

fi

if [ ${stage} -le 4 ] && [ ${stop_stage} -ge 4 ]; then
    log "stage 4: Create Non-linguistic Symbols for Language ID"
    cut -f 2- data/${train_set}/text | grep -o -P '\[.*?\]|\<.*?\>' | sort | uniq > ${nlsyms_txt}
    log "save non-linguistic symbols in ${nlsyms_txt}"
fi



log "Successfully finished. [elapsed=${SECONDS}s]"
