"""Parallel beam search module for online simulation."""

import logging
from pathlib import Path
from typing import List

import yaml

import torch

from espnet.nets.batch_beam_search import BatchBeamSearch
from espnet.nets.beam_search import Hypothesis
from espnet.nets.e2e_asr_common import end_detect


class BatchBeamSearchOnline(BatchBeamSearch):
    """Online beam search implementation.

    This simulates streaming decoding.
    It requires encoded features of entire utterance and
    extracts block by block from it as it shoud be done
    in streaming processing.
    This is based on Tsunoo et al, "STREAMING TRANSFORMER ASR
    WITH BLOCKWISE SYNCHRONOUS BEAM SEARCH"
    (https://arxiv.org/abs/2006.14941).
    """

    def __init__(self, *args, feature_width=0, text_width=0, **kwargs):
        super().__init__(*args, **kwargs)
        self.reset()
        self.feature_width = feature_width
        self.text_width = text_width

        self.block_size = 40
        self.hop_size = 16
        self.look_ahead = 16

    def reset(self):
        self.encbuffer = None

        self.running_hyps = None
        self.prev_hyps = []
        self.ended_hyps = []

        
    def forward(
        self,
        x: torch.Tensor,
        maxlenratio: float = 0.0,
        minlenratio: float = 0.0,
        is_final: bool = True
    ) -> List[Hypothesis]:
        """Perform beam search.

        Args:
            x (torch.Tensor): Encoded speech feature (T, D)
            maxlenratio (float): Input length ratio to obtain max output length.
                If maxlenratio=0.0 (default), it uses a end-detect function
                to automatically find maximum hypothesis lengths
            minlenratio (float): Input length ratio to obtain min output length.

        Returns:
            list[Hypothesis]: N-best decoding results

        """

        if self.running_hyps is None:
            self.running_hyps = self.init_hyp(x)
        
        if self.encbuffer is None:
            self.encbuffer = x
        else:
            self.encbuffer = torch.cat([self.encbuffer,x], axis=0)

        if is_final == False:
            return []
        else:
            x = self.encbuffer
            
        # set length bounds
        if maxlenratio == 0:
            maxlen = x.shape[0]
        else:
            maxlen = max(1, int(maxlenratio * x.size(0)))
        minlen = int(minlenratio * x.size(0))
        logging.info("decoder input length: " + str(x.shape[0]))
        logging.info("max output length: " + str(maxlen))
        logging.info("min output length: " + str(minlen))

        is_first = True
        while True:
            if is_first:
                cur_end_frame = int(self.block_size - self.look_ahead)
                self.process_idx = 0
                is_first = False
            else:
                if (
                        self.hop_size
                        and cur_end_frame + int(self.hop_size) + int(self.look_ahead)
                        < x.shape[0]
                ):
                    cur_end_frame += int(self.hop_size)
                else:
                    cur_end_frame = x.shape[0]
            
            if cur_end_frame < x.shape[0]:
                h = x.narrow(0, 0, cur_end_frame)
                is_final = False
            else:
                h = x
                is_final = True

            ret = self.process_one_block(h, is_final, maxlen, maxlenratio)
            if is_final:
                return ret

    def process_one_block(self, h, is_final, maxlen, maxlenratio):
        # extend states for ctc
        self.extend(h, self.running_hyps)

        while self.process_idx < maxlen:
            logging.debug("position " + str(self.process_idx))
            best = self.search(self.running_hyps, h)

            if self.process_idx == maxlen - 1:
                # end decoding
                self.running_hyps = self.post_process(
                    self.process_idx, maxlen, maxlenratio, best, self.ended_hyps
                )
            n_batch = best.yseq.shape[0]
            local_ended_hyps = []
            is_local_eos = (
                best.yseq[torch.arange(n_batch), best.length - 1] == self.eos
            )
                
            prev_repeat = False
            for i in range(is_local_eos.shape[0]):
                if is_local_eos[i]:
                    hyp = self._select(best, i)
                    local_ended_hyps.append(hyp)
                # NOTE(tsunoo): check repetitions here
                # This is a implicit implementation of
                # Eq (11) in https://arxiv.org/abs/2006.14941
                # A flag prev_repeat is used instead of using set
                elif (
                        not prev_repeat
                        and best.yseq[i, -1] in best.yseq[i, :-1]
                        and not is_final
                ):
                    prev_repeat = True
            if prev_repeat: break
                
            if maxlenratio == 0.0 and end_detect(
                    [lh.asdict() for lh in self.ended_hyps], self.process_idx
            ):
                logging.info(f"end detected at {self.process_idx}")
                return self.assemble_hyps(self.ended_hyps)

            if len(local_ended_hyps) > 0 and not is_final:
                break

            self.prev_hyps = self.running_hyps
            self.running_hyps = self.post_process(
                self.process_idx, maxlen, maxlenratio, best, self.ended_hyps
            )

            if is_final:
                for hyp in local_ended_hyps:
                    self.ended_hyps.append(hyp)

            if len(self.running_hyps) == 0:
                logging.info("no hypothesis. Finish decoding.")
                return self.assemble_hyps(self.ended_hyps)
            else:
                logging.debug(f"remained hypotheses: {len(self.running_hyps)}")
            # increment number
            self.process_idx += 1
        
        if is_final:
            return self.assemble_hyps(self.ended_hyps)
        else:
            logging.debug("Going to next block: %d", h.shape[0])
            if self.process_idx > 1 and len(self.prev_hyps) > 0:
                self.running_hyps = self.prev_hyps
                self.process_idx -= 1
                self.prev_hyps = []

            return None
        
    def assemble_hyps(self, ended_hyps):
        nbest_hyps = sorted(ended_hyps, key=lambda x: x.score, reverse=True)
        # check the number of hypotheses reaching to eos
        if len(nbest_hyps) == 0:
            logging.warning(
                "there is no N-best results, perform recognition "
                "again with smaller minlenratio."
            )
            return (
                []
                if minlenratio < 0.1
                else self.forward(x, maxlenratio, max(0.0, minlenratio - 0.1))
            )

        # report the best result
        best = nbest_hyps[0]
        for k, v in best.scores.items():
            logging.info(
                f"{v:6.2f} * {self.weights[k]:3} = {v * self.weights[k]:6.2f} for {k}"
            )
            logging.info(f"total log probability: {best.score:.2f}")
            logging.info(f"normalized log probability: {best.score / len(best.yseq):.2f}")
            logging.info(f"total number of ended hypotheses: {len(nbest_hyps)}")
            if self.token_list is not None:
                logging.info(
                    "best hypo: "
                    + "".join([self.token_list[x] for x in best.yseq[1:-1]])
                    + "\n"
                )
        return nbest_hyps
        
    def extend(self, x: torch.Tensor, hyps: Hypothesis) -> List[Hypothesis]:
        """Extend probabilities and states with more encoded chunks.

        Args:
            x (torch.Tensor): The extended encoder output feature
            hyps (Hypothesis): Current list of hypothesis

        Returns:
            Hypothesis: The exxtended hypothesis

        """
        for k, d in self.scorers.items():
            if hasattr(d, "extend_prob"):
                d.extend_prob(x)
            if hasattr(d, "extend_state"):
                hyps.states[k] = d.extend_state(hyps.states[k])
