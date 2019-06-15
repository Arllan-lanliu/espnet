#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright 2019 Tomoki Hayashi
#  Apache 2.0  (http://www.apache.org/licenses/LICENSE-2.0)

import torch

from espnet.nets.pytorch_backend.transformer.layer_norm import LayerNorm
from espnet.nets.tts_interface import TTSInterface


class DurationPredictorLoss(torch.nn.Module):
    """Duration predictor loss module

    Reference:
        - FastSpeech: Fast, Robust and Controllable Text to Speech
          (https://arxiv.org/pdf/1905.09263.pdf)
    """
    def __init__(self):
        super(DurationPredictorLoss, self).__init__()

    def forward(self):
        pass


class FeedForwardTransformer(TTSInterface, torch.nn.Module):
    """Feed Forward Transformer for TTS

    Reference:
        - FastSpeech: Fast, Robust and Controllable Text to Speech
          (https://arxiv.org/pdf/1905.09263.pdf)
    """

    def __init__(self):
        # initialize base classes
        TTSInterface.__init__(self)
        torch.nn.Module.__init__(self)

    def forward(self):
        pass


class LengthRegularizer(torch.nn.Module):
    """Lenght regularizer module

    Reference:
        - FastSpeech: Fast, Robust and Controllable Text to Speech
          (https://arxiv.org/pdf/1905.09263.pdf)
    """
    def __init__(self):
        super(LengthRegularizer, self).__init__()

    def forward(self):
        pass


class DurationPredictor(torch.nn.Module):
    """Duration predictor module

    Reference:
        - FastSpeech: Fast, Robust and Controllable Text to Speech
          (https://arxiv.org/pdf/1905.09263.pdf)

    :param int idim: input dimension
    :param int n_layers: number of convolutional layers
    :param int n_chans: number of channels of convolutional layers
    :param int kernel_size: kernel size of convolutional layers
    :param float dropout_rate: dropout rate
    """

    def __init__(self, idim, n_layers=2, n_chans=384, kernel_size=3, dropout_rate=0.1):
        super(DurationPredictor, self).__init__()
        self.conv = torch.nn.ModuleList()
        for idx in range(n_layers):
            in_chans = idim if idx == 0 else n_chans
            self.conv += [torch.nn.Sequential(
                torch.Conv1d(in_chans, n_chans, kernel_size, stride=1, padding=(kernel_size - 1) // 2),
                torch.nn.ReLU(),
                LayerNorm(n_chans, dim=1),
                torch.nn.Dropout(dropout_rate)
            )]
        self.linear = torch.nn.Linear(n_chans, 1)

    def forward(self, xs, x_masks=None):
        """Calculate duration predictor forward propagation

        :param torch.Tensor xs: input tensor (B, Tmax, idim)
        :param torch.Tensor x_masks: mask of input tensor (non-padded part should be 1) (B, Tmax)
        :return torch.Tensor: predicted duration tensor in log domain (B, Tmax, 1)
        """
        xs = xs.transpose(1, -1)  # (B, idim, Tmax)
        for idx in len(self.conv):
            xs = self.conv[idx](xs)  # (B, C, Tmax)
        xs = self.linear(xs.transpose(1, -1))  # (B, Tmax, 1)

        if x_masks is not None:
            x_masks = x_masks.eq(0).unsqueeze(-1)  # (B, Tmax, 1)
            xs = xs.masked_fill(x_masks, 0.0)

        return xs

    def inference(self, xs, x_masks=None):
        """Inference duration

        :param torch.Tensor xs: input tensor with tha shape (B, Tmax, idim)
        :param torch.Tensor x_masks: mask of input tensor (non-padded part should be 1) with the shape (B, Tmax)
        :return torch.Tensor: predicted duration tensor with the shape (B, Tmax, 1)
        """
        xs = xs.transpose(1, -1)  # (B, idim, Tmax)
        for idx in len(self.conv):
            xs = self.conv[idx](xs)  # (B, C, Tmax)
        xs = self.linear(xs.transpose(1, -1))  # (B, Tmax, 1)
        xs = torch.ceil(torch.exp(xs)).long()  # use ceil to avoid length = 0

        if x_masks is not None:
            x_masks = x_masks.eq(0).unsqueeze(-1)  # (B, Tmax, 1)
            xs = xs.masked_fill(x_masks, 0)

        return xs
