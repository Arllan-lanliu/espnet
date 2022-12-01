from abc import ABC, abstractmethod
from typing import Optional, Tuple

import torch


class AbsDiscriminator(torch.nn.Module, ABC):
    @abstractmethod
    def forward(
        self,
        xs_pad: torch.Tensor,
        padding_mask: torch.Tensor,
    ) -> torch.Tensor:
        raise NotImplementedError
