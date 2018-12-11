#!/bin/bash

# Copyright 2018 Kyoto University (Hirofumi Inaguma)
#  Apache 2.0  (http://www.apache.org/licenses/LICENSE-2.0)

export LC_ALL=C

. ./path.sh

if [ $# -lt 2 ]; then
   echo "Usage: $0 <LDC2010T04-location> <LDC96T17-location>"
   echo "e.g.: $0 /export/corpora/LDC/LDC2010T04 /export/corpora/LDC/LDC96T17"
   exit 1;
fi

# download data preparation scripts for transcriptions
[ ! -d data/local/fisher-callhome-corpus ] && git clone https://github.com/joshua-decoder/fisher-callhome-corpus.git data/local/fisher-callhome-corpus

# create symbolic links
cur_dir=`pwd`
cd local/
rm -rf mapping
ln -s $cur_dir/data/local/fisher-callhome-corpus/mapping .
cd $cur_dir

# data preparation for Es
cd data/local/fisher-callhome-corpus
./bin/build_fisher.sh $1/fisher_spa_tr
./bin/build_callhome.sh $2/callhome_spanish_trans_970711
cd $cur_dir


for set in fisher_train fisher_dev fisher_dev2 fisher_test callhome_train callhome_devtest callhome_evltest; do
    # concatenate short utterances
    cp data/$set/text data/$set/text.tmp
    cp data/$set/segments data/$set/segments.tmp
    local/concat_short_utt.py local/mapping/$set data/$set/text.tmp data/$set/segments.tmp
    rm data/$set/*.tmp

    # fix spk2utt and utt2spk
    ! cat data/$set/text | perl -ane 'm:([^-]+)-([AB])-(\S+)-(\S+): || die "Bad line $_;"; if ($3 != $4) {print "$1-$2-$3-$4 $1-$2\n"; }' > data/$set/utt2spk \
    && echo "Error producing utt2spk file" && exit 1;
    # NOTE: remove inappropriate segmentation (start and end points are the same)
    utils/utt2spk_to_spk2utt.pl < data/$set/utt2spk > data/$set/spk2utt

    # normalize punctuation
    cat data/local/fisher-callhome-corpus/corpus/ldc/$set.es > data/$set/es.joshua.org
    cat data/$set/es.joshua.org | normalize-punctuation.perl -l es | local/normalize_punctuation.perl > data/$set/es.joshua.norm
    lowercase.perl < data/$set/es.joshua.norm > data/$set/es.joshua.norm.lc
    tokenizer.perl -a -l es < data/$set/es.joshua.norm.lc.rm > data/$set/es.joshua.norm.lc.tok

    # Now checking these Es transcriptions are matching (double check)
    cat data/$set/text > data/$set/text.tmp
    cut -f 2- -d " " data/$set/text.tmp > data/$set/es.kaldi.org
    cat data/$set/es.kaldi.org | normalize-punctuation.perl -l es < local/normalize_punctuation.perl > data/$set/es.kaldi.norm
    lowercase.perl < data/$set/es.kaldi.norm > data/$set/es.kaldi.norm.lc
    tokenizer.perl -a -l es < data/$set/es.kaldi.norm.lc.rm > data/$set/es.kaldi.norm.lc.tok

    # use references from joshua-decoder/fisher-callhome-corpus
    paste -d " " <(awk '{print $1}' data/$set/text.tmp) <(cat data/$set/es.joshua.norm.lc.tok | awk '{if(NF>0) {print $0;} else {print "emptyuttrance";}}') \
      > data/$set/text.es

    # save original and cleaned punctuation
    cat data/$set/es.joshua.org | lowercase.perl | text2token.py -s 0 -n 1 | cut -f 2- -d " " | tr " " "\n" \
      | sort | uniq | grep -v -e '^\s*$' | awk '{print $0 " " NR+1}' > data/$set/punctuation.es
    cat data/$set/es.joshua.norm.lc | lowercase.perl | text2token.py -s 0 -n 1 | cut -f 2- -d " " | tr " " "\n" \
      | sort | uniq | grep -v -e '^\s*$' | awk '{print $0 " " NR+1}' > data/$set/punctuation.clean.es
done


# prepare En references
for set in fisher_train callhome_train callhome_devtest callhome_evltest; do
    # having one En reference
    cat data/local/fisher-callhome-corpus/corpus/ldc/$set.en > data/$set/en.org
    cat data/$set/en.org | normalize-punctuation.perl -l en | local/normalize_punctuation.pl > data/$set/en.norm
    lowercase.perl < data/$set/en.norm > data/$set/en.norm.lc
    tokenizer.perl -a -l en < data/$set/en.norm.lc.rm > data/$set/en.norm.lc.tok
    paste -d " " <(awk '{print $1}' data/$set/text.es) <(cat data/$set/en.norm.lc.tok | awk '{if(NF>0) {print $0;} else {print "emptyuttrance";}}') \
      > data/$set/text.en

    # save original and cleaned punctuation
    cat data/$set/en.org | lowercase.perl | text2token.py -s 0 -n 1 | cut -f 2- -d " " | tr " " "\n" \
      | sort | uniq | grep -v -e '^\s*$' | awk '{print $0 " " NR+1}' > data/$set/punctuation.en
    cat data/$set/en.norm.lc | lowercase.perl | text2token.py -s 0 -n 1 | cut -f 2- -d " " | tr " " "\n" \
      | sort | uniq | grep -v -e '^\s*$' | awk '{print $0 " " NR+1}' > data/$set/punctuation.clean.en
done
for set in fisher_dev fisher_dev2 fisher_test; do
    # having four En references
    for no in 0 1 2 3; do
        cat data/local/fisher-callhome-corpus/corpus/ldc/$set.en.${no} > data/$set/en.${no}.org
        cat data/$set/en.${no}.org | normalize-punctuation.perl -l en | local/normalize_punctuation.pl > data/$set/en.${no}.norm
        lowercase.perl < data/$set/en.${no}.norm > data/$set/en.${no}.norm.lc
        tokenizer.perl -a -l en < data/$set/en.${no}.norm.lc.rm > data/$set/en.${no}.norm.lc.tok
        paste -d " " <(awk '{print $1}' data/$set/text.es) <(cat data/$set/en.${no}.norm.lc.tok | awk '{if(NF>0) {print $0;} else {print "emptyuttrance";}}') \
          > data/$set/text.en.${no}
    done

    # save original and cleaned punctuation
    cat data/$set/en.*.org | lowercase.perl | text2token.py -s 0 -n 1 | cut -f 2- -d " " | tr " " "\n" \
      | sort | uniq | grep -v -e '^\s*$' | awk '{print $0 " " NR+1}' > data/$set/punctuation.en
    cat data/$set/en.*.norm.lc | lowercase.perl | text2token.py -s 0 -n 1 | cut -f 2- -d " " | tr " " "\n" \
      | sort | uniq | grep -v -e '^\s*$' | awk '{print $0 " " NR+1}' > data/$set/punctuation.clean.en
done


# error check
for set in fisher_train callhome_train callhome_devtest callhome_evltest; do
    n_es=`cat data/${set}/text.es | wc -l`
    n_en=`cat data/${set}/text.en | wc -l`
    [ $n_es -ne $n_en ] && echo "Warning: expected $n_es data data files, found $n_en" && exit 1;
done
for set in fisher_dev fisher_dev2 fisher_test; do
    n_es=`cat data/${set}/text.es | wc -l`
    for no in 0 1 2 3; do
        n_en=`cat data/${set}/text.en.${no} | wc -l`
        [ $n_es -ne $n_en ] && echo "Warning: expected $n_es data data files, found $n_en" && exit 1;
    done
done
# NOTE: the number of orignial utterances is as follow:
# fisher_train: 138819
# fisher_dev: 3979
# fisher_dev2: 3961
# fisher_test: 3641
# callhome_train: 15080
# callhome_devtest: 3966
# callhome_evltest: 1829


# remove empty and short utterances
for set in fisher_train fisher_dev fisher_dev2 fisher_test callhome_train callhome_devtest callhome_evltest; do
    cp -rf data/$set data/$set.tmp
    cat data/$set/text.es | grep -v emptyuttrance | cut -f 1 -d " " | sort > data/$set/reclist.es
    if [ -f data/$set/text.en ]; then
        cat data/$set/text.en | grep -v emptyuttrance | cut -f 1 -d " " | sort > data/$set/reclist.en
    else
        cat data/$set/text.en.0 | grep -v emptyuttrance | cut -f 1 -d " " | sort > data/$set/reclist.en.0
        cat data/$set/text.en.1 | grep -v emptyuttrance | cut -f 1 -d " " | sort > data/$set/reclist.en.1
        cat data/$set/text.en.2 | grep -v emptyuttrance | cut -f 1 -d " " | sort > data/$set/reclist.en.2
        cat data/$set/text.en.3 | grep -v emptyuttrance | cut -f 1 -d " " | sort > data/$set/reclist.en.3
        comm -12 data/$set/reclist.en.0 data/$set/reclist.en.1 > data/$set/reclist.en
        cp data/$set/reclist.en data/$set/reclist.en.tmp
        comm -12 data/$set/reclist.en.tmp data/$set/reclist.en.2 > data/$set/reclist.en
        cp data/$set/reclist.en data/$set/reclist.en.tmp
        comm -12 data/$set/reclist.en.tmp data/$set/reclist.en.3 > data/$set/reclist.en
    fi
    comm -12 data/$set/reclist.es data/$set/reclist.en > data/$set/reclist
    reduce_data_dir.sh data/$set.tmp data/$set/reclist data/$set
    if [ -f data/$set/text.en ]; then
        utils/fix_data_dir.sh --utt_extra_files "text.es text.en" data/$set
    else
        utils/fix_data_dir.sh --utt_extra_files "text.es text.en.0 text.en.1 text.en.2 text.en.3" data/$set
    fi
    rm -rf data/$set.tmp
done


echo "$0: successfully prepared data"
exit 0;
