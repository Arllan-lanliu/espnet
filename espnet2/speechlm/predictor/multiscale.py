#!/usr/bin/env python3

# Copyright 2024 Jinchuan Tian
#  Apache 2.0  (http://www.apache.org/licenses/LICENSE-2.0)

from typing import Tuple, Dict, List

import torch

from espnet2.speechlm.predictor.abs_predictor import AbsPredictor
from espnet2.speechlm.core_lm.builtin import BuiltinCoreLM


class MultiScalePredictor(AbsPredictor):
    """Local transformer part of https://arxiv.org/abs/2310.00704"""

    def __init__(
        self,
        vocab_size: List,
        input_dim: int,
        nq: int,
        pos_enc: str = None,
        att_unit: int = 256,
        head: int = 2,
        unit: int = 1024,
        decoder_layer: int = 4,
        dropout_rate: float = 0.1,
        positional_dropout_rate: float = 0.0,
        attention_dropout_rate: float = 0.0,
        flashattention: bool = False,
    ):
        super(MultiScalePredictor, self).__init__()

        self.decoder = BuiltinCoreLM(
            encoder_decoder_format=False,
            pos_enc=pos_enc,
            att_unit=att_unit,
            head=head,
            unit=unit,
            decoder_layer=decoder_layer,
            dropout_rate=dropout_rate,
            positional_dropout_rate=positional_dropout_rate,
            attention_dropout_rate=attention_dropout_rate,
            flashattention=flashattention,
        )

        # corelm output and embedding results should not be in the same space
        # so use two linear layers separately.
        if not input_dim == att_unit:
            self.linear_in_inp = torch.nn.Linear(input_dim, att_unit)
            self.linear_in_tgt = torch.nn.Linear(input_dim, att_unit)
            self.linear_out = torch.nn.Linear(att_unit, input_dim)
        else:
            self.linear_in_inp = None
            self.linear_in_tgt = None
            self.linear_out = None

        self.lm_head = torch.nn.Linear(input_dim, vocab_size, bias=False)

        self.nq = nq

    def get_lookup_table(self):
        return self.lm_head.weight

    def forward(
        self,
        input: torch.Tensor,
        input_lengths: torch.Tensor,
        target_emb: torch.Tensor,
        target_lengths: torch.Tensor,
        cache: dict = None,
    ) -> Tuple[torch.Tensor, torch.Tensor, Dict]:

        assert input.size(0) == target_emb.size(0)
        assert input.size(1) == target_emb.size(1)
        assert input.size(2) == target_emb.size(3)
        assert target_emb.size(2) == self.nq
        assert torch.all(torch.eq(input_lengths, target_lengths))

        if self.linear_in_inp:
            input = self.linear_in_inp(input)
        if self.linear_in_tgt:
            target_emb = self.linear_in_tgt(target_emb)

        # (2) resize and splice
        decoder_input = torch.cat([input.unsqueeze(2), target_emb], dim=2)[:, :, :-1]
        decoder_input = decoder_input.flatten(0, 1)

        # (3) decoder inference and resize
        decoder_output, _ = self.decoder(decoder_input)
        decoder_output = decoder_output.view(target_emb.size())

        if self.linear_out:
            decoder_output = self.linear_out(decoder_output)
        decoder_output = self.lm_head(decoder_output)

        return decoder_output, target_lengths
