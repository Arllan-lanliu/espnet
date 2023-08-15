python3 -m espnet2.bin.asr_inference --batch_size 1 --ngpu 0 --data_path_and_name_and_type dump/raw/test_asvspoof/wav.scp,speech,kaldi_ark --key_file exp/asr_train_asr_whisper_full_correct_specaug_raw_en_whisper_multilingual/decode_asr_fsd_asr_model_valid.acc.ave/test_asvspoof/logdir/keys.10.scp --asr_train_config exp/asr_train_asr_whisper_full_correct_specaug_raw_en_whisper_multilingual/config.yaml --asr_model_file exp/asr_train_asr_whisper_full_correct_specaug_raw_en_whisper_multilingual/valid.acc.ave.pth --output_dir exp/asr_train_asr_whisper_full_correct_specaug_raw_en_whisper_multilingual/decode_asr_fsd_asr_model_valid.acc.ave/test_asvspoof/logdir/output.10 --config conf/decode_asr_fsd.yaml 
python3 -m espnet2.bin.asr_inference --batch_size 1 --ngpu 0 --data_path_and_name_and_type dump/raw/test_asvspoof/wav.scp,speech,kaldi_ark --key_file exp/asr_train_asr_whisper_full_correct_specaug_raw_en_whisper_multilingual/decode_asr_fsd_asr_model_valid.acc.ave/test_asvspoof/logdir/keys.11.scp --asr_train_config exp/asr_train_asr_whisper_full_correct_specaug_raw_en_whisper_multilingual/config.yaml --asr_model_file exp/asr_train_asr_whisper_full_correct_specaug_raw_en_whisper_multilingual/valid.acc.ave.pth --output_dir exp/asr_train_asr_whisper_full_correct_specaug_raw_en_whisper_multilingual/decode_asr_fsd_asr_model_valid.acc.ave/test_asvspoof/logdir/output.11 --config conf/decode_asr_fsd.yaml 