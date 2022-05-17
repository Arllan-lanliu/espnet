#!/bin/bash

speaker_id=$1

if [ -z $speaker_id ]; then
    echo "Speaker id was not provided. Please provide a speaker id from the available ids: [a, b, c, d, e, f, g, h]"
    exit 1
fi

for dset in train_${speaker_id} dev_${speaker_id} eval1_${speaker_id}; do
    utils/copy_data_dir.sh data/"${dset}"{,_phn};
    srun ./pyscripts/utils/convert_text_to_phn.py --nj 1 --g2p g2p_is data/"${dset}"{,_phn}/text;
    # srun --gres=gpu:1 ./pyscripts/utils/convert_text_to_phn.py --nj 1 --g2p g2p_is --cleaner tacotron data/"${dset}"{,_phn}/text;
done