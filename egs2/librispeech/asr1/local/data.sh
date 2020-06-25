#!/bin/bash
# Set bash to 'debug' mode, it will exit on :
# -e 'error', -u 'undefined variable', -o ... 'error in pipeline', -x 'print commands',
set -e
set -u
set -o pipefail

log() {
    local fname=${BASH_SOURCE[1]##*/}
    echo -e "$(date '+%Y-%m-%dT%H:%M:%S') (${fname}:${BASH_LINENO[0]}:${FUNCNAME[1]}) $*"
}
help_message=$(cat << EOF
Usage: $0

Options:
    --lang (str): it, de, en, es, fr, it, nl, pt, ru
EOF
)
SECONDS=0


stage=1
stop_stage=100000
data_url=www.openslr.org/resources/12
train_set="train_960"
train_dev="dev"

log "$0 $*"
. utils/parse_options.sh

. ./db.sh
. ./path.sh
. ./cmd.sh


if [ $# -ne 0 ]; then
    log "${help_message}"
    log "Error: No positional arguments are required."
    exit 2
fi
if [ ! -e "${LIBRISPEECH}" ]; then
    log "Fill the value of 'LIBRISPEECH' of db.sh"
    exit 1
fi

if [ ${stage} -le 1 ] && [ ${stop_stage} -ge 1 ]; then
    if [ ! -e "${LIBRISPEECH}/LibriSpeech/LICENSE.TXT" ]; then
	echo "stage 1: Data Download to ${LIBRISPEECH}"
	for part in dev-clean test-clean dev-other test-other train-clean-100 train-clean-360 train-other-500; do
            local/download_and_untar.sh ${LIBRISPEECH} ${data_url} ${part}
	done
    else
        log "stage 1: ${LIBRISPEECH}/LibriSpeech/LICENSE.TXT is already existing. Skip data downloading"
    fi
fi

if [ ${stage} -le 2 ] && [ ${stop_stage} -ge 2 ]; then
    log "stage 2: Data Preparation"
    for part in dev-clean test-clean dev-other test-other train-clean-100 train-clean-360 train-other-500; do
        # use underscore-separated names in data directories.
        local/data_prep.sh ${LIBRISPEECH}/LibriSpeech/${part} data/${part//-/_}
    done
fi


if [ ${stage} -le 3 ] && [ ${stop_stage} -ge 3 ]; then
    log "stage 3: combine all training and development sets and remove long sentences"
    utils/combine_data.sh --extra_files utt2num_frames data/${train_set}_org data/train_clean_100 data/train_clean_360 data/train_other_500
    utils/combine_data.sh --extra_files utt2num_frames data/${train_dev}_org data/dev_clean data/dev_other

    # remove utt having more than 3000 frames
    # remove utt having more than 400 characters
    scripts/utils/remove_longshortdata.sh --maxframes 3000 --maxchars 400 data/${train_set}_org data/${train_set}
    scripts/utils/remove_longshortdata.sh --maxframes 3000 --maxchars 400 data/${train_dev}_org data/${train_dev}
fi

if [ ${stage} -le 4 ] && [ ${stop_stage} -ge 4 ]; then
    # use external data
    if [ ! -e data/local/other_text/librispeech-lm-norm.txt.gz ]; then
	log "stage 4: prepare external text data from http://www.openslr.org/resources/11/librispeech-lm-norm.txt.gz"
        wget http://www.openslr.org/resources/11/librispeech-lm-norm.txt.gz -P data/local/other_text/
        zcat data/local/other_text/librispeech-lm-norm.txt.gz > data/local/other_text/text
    fi
fi

log "Successfully finished. [elapsed=${SECONDS}s]"
