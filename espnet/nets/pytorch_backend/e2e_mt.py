#!/usr/bin/env python
# encoding: utf-8

# Copyright 2019 Kyoto University (Hirofumi Inaguma)
#  Apache 2.0  (http://www.apache.org/licenses/LICENSE-2.0)


from __future__ import division

import argparse
import logging
import math
import os

import chainer
import numpy as np
import six
import torch

from chainer import reporter
from nltk.translate import bleu_score

from espnet.nets.mt_interface import MTInterface
from espnet.nets.e2e_asr_common import label_smoothing_dist


from espnet.nets.pytorch_backend.nets_utils import pad_list
from espnet.nets.pytorch_backend.nets_utils import to_device
from espnet.nets.pytorch_backend.rnn.attentions import att_for
from espnet.nets.pytorch_backend.rnn.decoders import decoder_for
from espnet.nets.pytorch_backend.rnn.encoders import encoder_for


class Reporter(chainer.Chain):
    """A chainer reporter wrapper"""

    def report(self, loss, acc, ppl, bleu):
        reporter.report({'loss': loss}, self)
        reporter.report({'acc': acc}, self)
        reporter.report({'ppl': ppl}, self)
        reporter.report({'bleu': bleu}, self)


class E2E(MTInterface, torch.nn.Module):
    """E2E module

    :param int idim: dimension of inputs
    :param int odim: dimension of outputs
    :param Namespace args: argument Namespace containing options
    """

    def __init__(self, idim, odim, args):
        super(E2E, self).__init__()
        torch.nn.Module.__init__(self)
        self.etype = args.etype
        self.verbose = args.verbose
        self.char_list = args.char_list
        self.outdir = args.outdir
        self.reporter = Reporter()

        # below means the last number becomes eos/sos ID
        # note that sos/eos IDs are identical
        self.sos = odim - 1
        self.eos = odim - 1
        self.pad = odim

        # subsample info
        # +1 means input (+1) and layers outputs (args.elayer)
        subsample = np.ones(args.elayers + 1, dtype=np.int)
        logging.warning('Subsampling is not performed for machine translation.')
        logging.info('subsample: ' + ' '.join([str(x) for x in subsample]))
        self.subsample = subsample

        # label smoothing info
        if args.lsm_type and os.path.isfile(args.train_json):
            logging.info("Use label smoothing with " + args.lsm_type)
            labeldist = label_smoothing_dist(odim, args.lsm_type, transcript=args.train_json)
        else:
            labeldist = None

        # multilingual related
        self.replace_sos = args.replace_sos

        # encoder
        self.embed_src = torch.nn.Embedding(idim + 1, args.eunits, padding_idx=idim)
        # NOTE: +1 means the padding index
        self.dropout_emb_src = torch.nn.Dropout(p=args.dropout_rate)
        self.enc = encoder_for(args, args.dunits, self.subsample)
        # attention
        self.att = att_for(args)
        # decoder
        self.dec = decoder_for(args, odim, self.sos, self.eos, self.att, labeldist)

        # weight initialization
        self.init_like_chainer()

        # options for beam search
        if 'report_bleu' in vars(args) and args.report_bleu:
            recog_args = {'beam_size': args.beam_size, 'penalty': args.penalty,
                          'ctc_weight': 0.0, 'maxlenratio': args.maxlenratio,
                          'minlenratio': args.minlenratio, 'lm_weight': args.lm_weight,
                          'rnnlm': args.rnnlm, 'nbest': args.nbest,
                          'space': args.sym_space, 'blank': args.sym_blank,
                          'tgt_lang': False}

            self.recog_args = argparse.Namespace(**recog_args)
            self.report_bleu = args.report_bleu
        else:
            self.report_bleu = False
        self.rnnlm = None

        self.logzero = -10000000000.0
        self.loss = None
        self.acc = None

    def init_like_chainer(self):
        """Initialize weight like chainer

        chainer basically uses LeCun way: W ~ Normal(0, fan_in ** -0.5), b = 0
        pytorch basically uses W, b ~ Uniform(-fan_in**-0.5, fan_in**-0.5)

        however, there are two exceptions as far as I know.
        - EmbedID.W ~ Normal(0, 1)
        - LSTM.upward.b[forget_gate_range] = 1 (but not used in NStepLSTM)
        """

        def lecun_normal_init_parameters(module):
            for p in module.parameters():
                data = p.data
                if data.dim() == 1:
                    # bias
                    data.zero_()
                elif data.dim() == 2:
                    # linear weight
                    n = data.size(1)
                    stdv = 1. / math.sqrt(n)
                    data.normal_(0, stdv)
                elif data.dim() in (3, 4):
                    # conv weight
                    n = data.size(1)
                    for k in data.size()[2:]:
                        n *= k
                    stdv = 1. / math.sqrt(n)
                    data.normal_(0, stdv)
                else:
                    raise NotImplementedError

        def set_forget_bias_to_one(bias):
            n = bias.size(0)
            start, end = n // 4, n // 2
            bias.data[start:end].fill_(1.)

        lecun_normal_init_parameters(self)
        # exceptions
        # embed weight ~ Normal(0, 1)
        self.dec.embed.weight.data.normal_(0, 1)
        # forget-bias = 1.0
        # https://discuss.pytorch.org/t/set-forget-gate-bias-of-lstm/1745
        for l in six.moves.range(len(self.dec.decoder)):
            set_forget_bias_to_one(self.dec.decoder[l].bias_ih)

    def forward(self, xs_pad, ilens, ys_pad):
        """E2E forward

        :param torch.Tensor xs_pad: batch of padded input sequences (B, Tmax, idim)
        :param torch.Tensor ilens: batch of lengths of input sequences (B)
        :param torch.Tensor ys_pad: batch of padded character id sequence tensor (B, Lmax)
        :rtype: torch.Tensor
        :return: attention loss value
        :rtype: torch.Tensor
        :return: accuracy in attention decoder
        :rtype: float
        """
        # 1. Encoder
        if self.replace_sos:
            # remove source language ID in the beggining
            tgt_lang_ids = ys_pad[:, 0:1]
            ys_pad = ys_pad[:, 1:]  # remove target language ID in the beginning
            xs_pad_emb = self.dropout_emb_src(self.embed_src(xs_pad[:, 1:]))
            ilens -= 1
        else:
            xs_pad_emb = self.dropout_emb_src(self.embed_src(xs_pad))
            tgt_lang_ids = None
        hs_pad, hlens, _ = self.enc(xs_pad_emb, ilens)

        # 3. attention loss
        loss, acc, ppl = self.dec(hs_pad, hlens, ys_pad, tgt_lang_ids=tgt_lang_ids)
        self.acc = acc
        self.ppl = ppl

        # 5. compute bleu without beam search
        if self.training or not self.report_bleu:
            bleu = 0.0
        else:
            bleus = []
            nbest_hyps = self.dec.recognize_beam_batch(hs_pad, torch.tensor(hlens), None,
                                                       self.recog_args, self.char_list,
                                                       self.rnnlm,
                                                       tgt_lang_ids=tgt_lang_ids.squeeze(1).tolist() if self.replace_sos else None)
            # remove <sos> and <eos>
            y_hats = [nbest_hyp[0]['yseq'][1:-1] for nbest_hyp in nbest_hyps]
            for i, y_hat in enumerate(y_hats):
                y_true = ys_pad[i]

                seq_hat = [self.char_list[int(idx)] for idx in y_hat if int(idx) != -1]
                seq_true = [self.char_list[int(idx)] for idx in y_true if int(idx) != -1]
                seq_hat_text = "".join(seq_hat).replace(self.recog_args.space, ' ')
                seq_hat_text = seq_hat_text.replace(self.recog_args.blank, '')
                seq_true_text = "".join(seq_true).replace(self.recog_args.space, ' ')

                hyp_words = seq_hat_text.split()
                ref_words = seq_true_text.split()

                bleus.append(bleu_score.sentence_bleu([ref_words], hyp_words))

            bleu = 0.0 if not self.report_bleu else sum(bleus) / len(bleus)

        self.loss = loss

        loss_data = float(self.loss)
        if not math.isnan(loss_data):
            self.reporter.report(loss_data, acc, ppl, bleu)
        else:
            logging.warning('loss (=%f) is not correct', loss_data)
        return self.loss

    def translate(self, x, recog_args, char_list, rnnlm=None):
        """E2E beam search

        :param ndarray x: input source text feature (T, D)
        :param Namespace recog_args: argument Namespace containing options
        :param list char_list: list of characters
        :param torch.nn.Module rnnlm: language model module
        :return: N-best decoding results
        :rtype: list
        """
        prev = self.training
        self.eval()
        ilen = [x.shape[0]]

        # 1. encoder
        # make a utt list (1) to use the same interface for encoder
        if self.replace_sos:
            id2token = {i: x for i, x in enumerate(char_list)}
            logging.info('src (multilingual): %s', ' '.join([id2token[int(y)] for y in x[0][1:]]))
            h = to_device(self, torch.from_numpy(np.fromiter(map(int, x[0][1:]), dtype=np.int64)))
            h = h.contiguous()
            h_emb = self.dropout_emb_src(self.embed_src(h.unsqueeze(0)))
        else:
            h = to_device(self, torch.from_numpy(np.fromiter(map(int, x[0]), dtype=np.int64)))
            h = h.contiguous()
            h_emb = self.dropout_emb_src(self.embed_src(h.unsqueeze(0)))
        hs, _, _ = self.enc(h_emb, ilen)

        # 2. decoder
        # decode the first utterance
        y = self.dec.recognize_beam(hs[0], None, recog_args, char_list, rnnlm)

        if prev:
            self.train()
        return y

    def translate_batch(self, xs, recog_args, char_list, rnnlm=None):
        """E2E beam search

        :param list xs: list of input source text feature arrays [(T_1, D), (T_2, D), ...]
        :param Namespace recog_args: argument Namespace containing options
        :param list char_list: list of characters
        :param torch.nn.Module rnnlm: language model module
        :return: N-best decoding results
        :rtype: list
        """
        prev = self.training
        self.eval()
        ilens = np.fromiter((xx.shape[0] for xx in xs), dtype=np.int64)

        # 1. Encoder
        if self.replace_sos:
            hs = [to_device(self, torch.from_numpy(xx)) for xx in xs]
            xpad = pad_list(hs, self.pad)
            xpad_emb = self.dropout_emb_src(self.embed_src(xpad))
        else:
            hs = [to_device(self, torch.from_numpy(xx)) for xx in xs]
            xpad = pad_list(hs, self.pad)
            xpad_emb = self.dropout_emb_src(self.embed_src(xpad))
        hs_pad, hlens, _ = self.enc(xpad_emb, ilens)

        # 2. Decoder
        hlens = torch.tensor(list(map(int, hlens)))  # make sure hlens is tensor
        y = self.dec.recognize_beam_batch(hs_pad, hlens, None, recog_args, char_list, rnnlm)

        if prev:
            self.train()
        return y

    def calculate_all_attentions(self, xs_pad, ilens, ys_pad):
        """E2E attention calculation

        :param torch.Tensor xs_pad: batch of padded input sequences (B, Tmax, idim)
        :param torch.Tensor ilens: batch of lengths of input sequences (B)
        :param torch.Tensor ys_pad: batch of padded character id sequence tensor (B, Lmax)
        :return: attention weights with the following shape,
            1) multi-head case => attention weights (B, H, Lmax, Tmax),
            2) other case => attention weights (B, Lmax, Tmax).
        :rtype: float ndarray
        """
        with torch.no_grad():
            # 1. Encoder
            if self.replace_sos:
                # remove source language ID in the beggining
                tgt_lang_ids = ys_pad[:, 0:1]
                ys_pad = ys_pad[:, 1:]  # remove target language ID in the beggining
                xs_pad_emb = self.dropout_emb_src(self.embed_src(xs_pad[:, 1:]))
                ilens -= 1
            else:
                xs_pad_emb = self.dropout_emb_src(self.embed_src(xs_pad))
                tgt_lang_ids = None
            hpad, hlens, _ = self.enc(xs_pad_emb, ilens)

            # 2. Decoder
            att_ws = self.dec.calculate_all_attentions(hpad, hlens, ys_pad, tgt_lang_ids=tgt_lang_ids)

        return att_ws
