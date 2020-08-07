#!/bin/bash
# Copyright 2020 Electronics and Telecommunications Research Institute (Hoon Chung)
# Apache 2.0  (http://www.apache.org/licenses/LICENSE-2.0)

# Set bash to 'debug' mode, it will exit on :
# -e 'error', -u 'undefined variable', -o ... 'error in pipeline', -x 'print commands',
set -e
set -u
set -o pipefail

log() {
    local fname=${BASH_SOURCE[1]##*/}
    echo -e "$(date '+%Y-%m-%dT%H:%M:%S') (${fname}:${BASH_LINENO[0]}:${FUNCNAME[1]}) $*"
}
SECONDS=0

stage=1
stop_stage=100

datadir=./downloads
ndev_utt=220

log "$0 $*"
. utils/parse_options.sh

if [ $# -ne 0 ]; then
    log "Error: No positional arguments are required."
    exit 2
fi

. ./path.sh
. ./cmd.sh

train_set="train_nodev"
train_dev="train_dev"
test_set="test_clean"

if [ ${stage} -le 1 ] && [ ${stop_stage} -ge 1 ]; then
    log "stage 1: Data Download"
    mkdir -p ${datadir}
    local/download_and_untar.sh ${datadir}
fi

if [ $stage -le 2 ] && [ ${stop_stage} -ge 2 ]; then
  # format the data as Kaldi data directories
  for part in train_data_01 test_data_01; do
  	# use underscore-separated names in data directories.
  	local/data_prep.sh ${datadir} ${part}
  done
    
  # shuffle whole training set
  utils/shuffle_list.pl data/train_data_01/utt2spk > utt2spk.tmp

  # make a dev set
  head -${ndev_utt} utt2spk.tmp | \
  utils/subset_data_dir.sh --utt-list - data/train_data_01 "data/${train_dev}"

  # make a traing set
  n=$(($(wc -l < data/train_data_01/text) - ndev_utt))
  tail -${n} utt2spk.tmp | \
  utils/subset_data_dir.sh --utt-list - data/train_data_01 "data/${train_set}"

  rm -f utt2spk.tmp

  # copy a test set
  utils/copy_data_dir.sh data/test_data_01 "data/${test_set}"
fi

log "Successfully finished. [elapsed=${SECONDS}s]"
