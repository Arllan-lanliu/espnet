#!/usr/bin/env python3
# encoding: utf-8

# Copyright 2017 Tomoki Hayashi (Nagoya University)
#  Apache 2.0  (http://www.apache.org/licenses/LICENSE-2.0)

import configargparse
import logging
import os
import platform
import random
import subprocess
import sys

import numpy as np

from espnet.utils.cli_utils import strtobool
from espnet.utils.training.batchfy import BATCH_COUNT_CHOICES


# NOTE: you need this func to generate our sphinx doc
def get_parser():
    parser = configargparse.ArgumentParser(
        description="Train an automatic speech recognition (ASR) model on one CPU, one or multiple GPUs",
        config_file_parser_class=configargparse.YAMLConfigFileParser,
        formatter_class=configargparse.ArgumentDefaultsHelpFormatter)
    # general configuration
    parser.add('--config', is_config_file=True, help='config file path')
    parser.add('--config2', is_config_file=True,
               help='second config file path that overwrites the settings in `--config`.')
    parser.add('--config3', is_config_file=True,
               help='third config file path that overwrites the settings in `--config` and `--config2`.')

    parser.add_argument('--ngpu', default=0, type=int,
                        help='Number of GPUs')
    parser.add_argument('--backend', default='chainer', type=str,
                        choices=['chainer', 'pytorch'],
                        help='Backend library')
    parser.add_argument('--outdir', type=str, required=True,
                        help='Output directory')
    parser.add_argument('--debugmode', default=1, type=int,
                        help='Debugmode')
    parser.add_argument('--dict', required=True,
                        help='Dictionary')
    parser.add_argument('--seed', default=1, type=int,
                        help='Random seed')
    parser.add_argument('--debugdir', type=str,
                        help='Output directory for debugging')
    parser.add_argument('--resume', '-r', default='', nargs='?',
                        help='Resume the training from snapshot')
    parser.add_argument('--minibatches', '-N', type=int, default='-1',
                        help='Process only N minibatches (for debug)')
    parser.add_argument('--verbose', '-V', default=0, type=int,
                        help='Verbose option')
    parser.add_argument('--tensorboard-dir', default=None, type=str, nargs='?', help="Tensorboard log dir path")
    # task related
    parser.add_argument('--train-json', type=str, default=None,
                        help='Filename of train label data (json)')
    parser.add_argument('--valid-json', type=str, default=None,
                        help='Filename of validation label data (json)')
    # network architecture
    parser.add_argument('--model-module', type=str, default=None,
                        help='model defined module (default: espnet.nets.xxx_backend.e2e_asr:E2E)')
    # minibatch related
    parser.add_argument('--sortagrad', default=0, type=int, nargs='?',
                        help="How many epochs to use sortagrad for. 0 = deactivated, -1 = all epochs")
    parser.add_argument('--batch-count', default='auto', choices=BATCH_COUNT_CHOICES,
                        help='How to count batch_size. The default (auto) will find how to count by args.')
    parser.add_argument('--batch-size', '--batch-seqs', '-b', default=0, type=int,
                        help='Maximum seqs in a minibatch (0 to disable)')
    parser.add_argument('--batch-bins', default=0, type=int,
                        help='Maximum bins in a minibatch (0 to disable)')
    parser.add_argument('--batch-frames-in', default=0, type=int,
                        help='Maximum input frames in a minibatch (0 to disable)')
    parser.add_argument('--batch-frames-out', default=0, type=int,
                        help='Maximum output frames in a minibatch (0 to disable)')
    parser.add_argument('--batch-frames-inout', default=0, type=int,
                        help='Maximum input+output frames in a minibatch (0 to disable)')
    parser.add_argument('--maxlen-in', '--batch-seq-maxlen-in', default=800, type=int, metavar='ML',
                        help='When --batch-count=seq, batch size is reduced if the input sequence length > ML.')
    parser.add_argument('--maxlen-out', '--batch-seq-maxlen-out', default=150, type=int, metavar='ML',
                        help='When --batch-count=seq, batch size is reduced if the output sequence length > ML')
    parser.add_argument('--n-iter-processes', default=0, type=int,
                        help='Number of processes of iterator')
    parser.add_argument('--preprocess-conf', type=str, default=None, nargs='?',
                        help='The configuration file for the pre-processing')
    # optimization related
    parser.add_argument('--opt', default='adadelta', type=str,
                        choices=['adadelta', 'adam', 'noam'],
                        help='Optimizer')
    parser.add_argument('--accum-grad', default=1, type=int,
                        help='Number of gradient accumuration')
    parser.add_argument('--eps', default=1e-8, type=float,
                        help='Epsilon constant for optimizer')
    parser.add_argument('--eps-decay', default=0.01, type=float,
                        help='Decaying ratio of epsilon')
    parser.add_argument('--weight-decay', default=0.0, type=float,
                        help='Weight decay ratio')
    parser.add_argument('--criterion', default='acc', type=str,
                        choices=['loss', 'acc'],
                        help='Criterion to perform epsilon decay')
    parser.add_argument('--threshold', default=1e-4, type=float,
                        help='Threshold to stop iteration')
    parser.add_argument('--epochs', '-e', default=30, type=int,
                        help='Maximum number of epochs')
    parser.add_argument('--early-stop-criterion', default='validation/main/acc', type=str, nargs='?',
                        help="Value to monitor to trigger an early stopping of the training")
    parser.add_argument('--patience', default=3, type=int, nargs='?',
                        help="Number of epochs to wait without improvement before stopping the training")
    parser.add_argument('--grad-clip', default=5, type=float,
                        help='Gradient norm threshold to clip')
    parser.add_argument('--num-save-attention', default=3, type=int,
                        help='Number of samples of attention to be saved')
    parser.add_argument('--grad-noise', type=strtobool, default=False,
                        help='The flag to switch to use noise injection to gradients during training')
    # speech translation related
    parser.add_argument('--context-residual', default=False, type=strtobool, nargs='?',
                        help='The flag to switch to use context vector residual in the decoder network')
    parser.add_argument('--asr-model', default=None, type=str, nargs='?',
                        help='Pre-trained ASR model')
    parser.add_argument('--mt-model', default=None, type=str, nargs='?',
                        help='Pre-trained MT model')
    parser.add_argument('--replace-sos', default=False, nargs='?',
                        help='Replace <sos> in the decoder with a target language ID \
                              (the first token in the target sequence)')

    # front end related
    parser.add_argument('--use-frontend', type=strtobool, default=False,
                        help='The flag to switch to use frontend system.')

    # WPE related
    parser.add_argument('--use-wpe', type=strtobool, default=False,
                        help='Apply Weighted Prediction Error')
    parser.add_argument('--wtype', default='blstmp', type=str,
                        choices=['lstm', 'blstm', 'lstmp', 'blstmp', 'vgglstmp', 'vggblstmp', 'vgglstm', 'vggblstm',
                                 'gru', 'bgru', 'grup', 'bgrup', 'vgggrup', 'vggbgrup', 'vgggru', 'vggbgru'],
                        help='Type of encoder network architecture '
                             'of the mask estimator for WPE. '
                             '')
    parser.add_argument('--wlayers', type=int, default=2,
                        help='')
    parser.add_argument('--wunits', type=int, default=300,
                        help='')
    parser.add_argument('--wprojs', type=int, default=300,
                        help='')
    parser.add_argument('--wdropout-rate', type=float, default=0.0,
                        help='')
    parser.add_argument('--wpe-taps', type=int, default=5,
                        help='')
    parser.add_argument('--wpe-delay', type=int, default=3,
                        help='')
    parser.add_argument('--use-dnn-mask-for-wpe', type=strtobool,
                        default=False,
                        help='Use DNN to estimate the power spectrogram. '
                             'This option is experimental.')
    # Beamformer related
    parser.add_argument('--use-beamformer', type=strtobool,
                        default=True, help='')
    parser.add_argument('--btype', default='blstmp', type=str,
                        choices=['lstm', 'blstm', 'lstmp', 'blstmp', 'vgglstmp', 'vggblstmp', 'vgglstm', 'vggblstm',
                                 'gru', 'bgru', 'grup', 'bgrup', 'vgggrup', 'vggbgrup', 'vgggru', 'vggbgru'],
                        help='Type of encoder network architecture '
                             'of the mask estimator for Beamformer.')
    parser.add_argument('--blayers', type=int, default=2,
                        help='')
    parser.add_argument('--bunits', type=int, default=300,
                        help='')
    parser.add_argument('--bprojs', type=int, default=300,
                        help='')
    parser.add_argument('--badim', type=int, default=320,
                        help='')
    parser.add_argument('--ref-channel', type=int, default=-1,
                        help='The reference channel used for beamformer. '
                             'By default, the channel is estimated by DNN.')
    parser.add_argument('--bdropout-rate', type=float, default=0.0,
                        help='')
    # Feature transform: Normalization
    parser.add_argument('--stats-file', type=str, default=None,
                        help='The stats file for the feature normalization')
    parser.add_argument('--apply-uttmvn', type=strtobool, default=True,
                        help='Apply utterance level mean '
                             'variance normalization.')
    parser.add_argument('--uttmvn-norm-means', type=strtobool,
                        default=True, help='')
    parser.add_argument('--uttmvn-norm-vars', type=strtobool, default=False,
                        help='')
    # Feature transform: Fbank
    parser.add_argument('--fbank-fs', type=int, default=16000,
                        help='The sample frequency used for '
                             'the mel-fbank creation.')
    parser.add_argument('--n-mels', type=int, default=80,
                        help='The number of mel-frequency bins.')
    parser.add_argument('--fbank-fmin', type=float, default=0.,
                        help='')
    parser.add_argument('--fbank-fmax', type=float, default=None,
                        help='')

    return parser


def main(cmd_args):
    parser = get_parser()
    args, _ = parser.parse_known_args(cmd_args)

    from espnet.utils.dynamic_import import dynamic_import
    if args.model_module is None:
        model_module = "espnet.nets." + args.backend + "_backend.e2e_asr:E2E"
    else:
        model_module = args.model_module
    model_class = dynamic_import(model_module)
    model_class.add_arguments(parser)

    args = parser.parse_args(cmd_args)
    args.model_module = model_module
    if 'chainer_backend' in args.model_module:
        args.backend = 'chainer'
    if 'pytorch_backend' in args.model_module:
        args.backend = 'pytorch'

    # logging info
    if args.verbose > 0:
        logging.basicConfig(
            level=logging.INFO, format='%(asctime)s (%(module)s:%(lineno)d) %(levelname)s: %(message)s')
    else:
        logging.basicConfig(
            level=logging.WARN, format='%(asctime)s (%(module)s:%(lineno)d) %(levelname)s: %(message)s')
        logging.warning('Skip DEBUG/INFO messages')

    # check CUDA_VISIBLE_DEVICES
    if args.ngpu > 0:
        # python 2 case
        if platform.python_version_tuple()[0] == '2':
            if "clsp.jhu.edu" in subprocess.check_output(["hostname", "-f"]):
                cvd = subprocess.check_output(["/usr/local/bin/free-gpu", "-n", str(args.ngpu)]).strip()
                logging.info('CLSP: use gpu' + cvd)
                os.environ['CUDA_VISIBLE_DEVICES'] = cvd
        # python 3 case
        else:
            if "clsp.jhu.edu" in subprocess.check_output(["hostname", "-f"]).decode():
                cvd = subprocess.check_output(["/usr/local/bin/free-gpu", "-n", str(args.ngpu)]).decode().strip()
                logging.info('CLSP: use gpu' + cvd)
                os.environ['CUDA_VISIBLE_DEVICES'] = cvd
        cvd = os.environ.get("CUDA_VISIBLE_DEVICES")
        if cvd is None:
            logging.warning("CUDA_VISIBLE_DEVICES is not set.")
        elif args.ngpu != len(cvd.split(",")):
            logging.error("#gpus is not matched with CUDA_VISIBLE_DEVICES.")
            sys.exit(1)

    # display PYTHONPATH
    logging.info('python path = ' + os.environ.get('PYTHONPATH', '(None)'))

    # set random seed
    logging.info('random seed = %d' % args.seed)
    random.seed(args.seed)
    np.random.seed(args.seed)

    # load dictionary for debug log
    if args.dict is not None:
        with open(args.dict, 'rb') as f:
            dictionary = f.readlines()
        char_list = [entry.decode('utf-8').split(' ')[0]
                     for entry in dictionary]
        char_list.insert(0, '<blank>')
        char_list.append('<eos>')
        args.char_list = char_list
    else:
        args.char_list = None

    # train
    logging.info('backend = ' + args.backend)
    if not hasattr(args, 'num_spkrs'):
        if args.backend == "chainer":
            from espnet.asr.chainer_backend.asr import train
            train(args)
        elif args.backend == "pytorch":
            from espnet.asr.pytorch_backend.asr import train
            train(args)
        else:
            raise ValueError("Only chainer and pytorch are supported.")
    else:
        if args.backend == "pytorch":
            from espnet.asr.pytorch_backend.asr_mix import train
            train(args)
        else:
            raise ValueError("Only pytorch is supported.")


if __name__ == '__main__':
    main(sys.argv[1:])
