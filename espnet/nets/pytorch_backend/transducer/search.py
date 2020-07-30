"""Search algorithms for transducer models."""

import numpy as np

import torch
import torch.nn.functional as F

from espnet.nets.pytorch_backend.transducer.utils import get_batch_lm_states
from espnet.nets.pytorch_backend.transducer.utils import get_idx_lm_state
from espnet.nets.pytorch_backend.transducer.utils import is_prefix
from espnet.nets.pytorch_backend.transducer.utils import recombine_hyps
from espnet.nets.pytorch_backend.transducer.utils import substract


def greedy_search(decoder, h, recog_args):
    """Greedy search implementation for transformer-transducer.

    Args:
        decoder (class): decoder class
        h (torch.Tensor): encoder hidden state sequences (maxlen_in, Henc)
        recog_args (Namespace): argument Namespace containing options

    Returns:
        hyp (list of dicts): 1-best decoding results

    """
    init_tensor = h.unsqueeze(0)
    state = decoder.zero_state(init_tensor)

    hyp = {"score": 0.0, "yseq": [decoder.blank], "dec_state": state, "att_w": None}

    y, state, att_w, _ = decoder.forward_one_step(hyp, init_tensor)

    for i, hi in enumerate(h):
        ytu = torch.log_softmax(decoder.joint(hi, y[0]), dim=0)
        logp, pred = torch.max(ytu, dim=0)

        if pred != decoder.blank:
            hyp["yseq"].append(int(pred))
            hyp["score"] += float(logp)

            hyp["dec_state"] = state
            hyp["att_w"] = att_w if att_w is None else att_w[:]

            y, state, att_w, _ = decoder.forward_one_step(hyp, init_tensor)

    return [hyp]


def default_beam_search(decoder, h, recog_args, rnnlm=None):
    """Beam search implementation.

    Args:
        decoder (class): decoder class
        h (torch.Tensor): encoder hidden state sequences (Tmax, Henc)
        recog_args (Namespace): argument Namespace containing options
        rnnlm (torch.nn.Module): language module

    Returns:
        nbest_hyps (list of dicts): n-best decoding results

    """
    beam = recog_args.beam_size
    k_range = min(beam, decoder.odim)

    nbest = recog_args.nbest
    normscore = recog_args.score_norm_transducer

    init_tensor = h.unsqueeze(0)
    state = decoder.zero_state(init_tensor)

    kept_hyps = [
        {
            "score": 0.0,
            "yseq": [decoder.blank],
            "dec_state": state,
            "lm_state": None,
            "att_w": None,
        }
    ]

    for hi in h:
        hyps = kept_hyps
        kept_hyps = []

        while True:
            new_hyp = max(hyps, key=lambda x: x["score"])
            hyps.remove(new_hyp)

            y, state, att_w, lm_tokens = decoder.forward_one_step(new_hyp, init_tensor)

            ytu = F.log_softmax(decoder.joint(hi, y[0]), dim=0)

            if rnnlm:
                rnnlm_state, rnnlm_scores = rnnlm.predict(
                    new_hyp["lm_state"], lm_tokens
                )

            for k in range(decoder.odim):
                beam_hyp = {
                    "score": new_hyp["score"] + float(ytu[k]),
                    "yseq": new_hyp["yseq"][:],
                    "dec_state": new_hyp["dec_state"],
                    "lm_state": new_hyp["lm_state"],
                    "att_w": new_hyp["att_w"],
                }

                if k == decoder.blank:
                    kept_hyps.append(beam_hyp)
                else:
                    beam_hyp["dec_state"] = state
                    beam_hyp["att_w "] = att_w if att_w is None else att_w[:]

                    beam_hyp["yseq"].append(int(k))

                    if rnnlm:
                        beam_hyp["lm_state"] = rnnlm_state
                        beam_hyp["score"] += recog_args.lm_weight * rnnlm_scores[0][k]

                    hyps.append(beam_hyp)

            hyps_max = float(max(hyps, key=lambda x: x["score"])["score"])
            kept_most_prob = len(
                sorted(kept_hyps, key=lambda x: float(x["score"]) > hyps_max)
            )
            if kept_most_prob >= k_range:
                break

    if normscore:
        nbest_hyps = sorted(
            kept_hyps, key=lambda x: x["score"] / len(x["yseq"]), reverse=True
        )[:nbest]
    else:
        nbest_hyps = sorted(kept_hyps, key=lambda x: x["score"], reverse=True)[:nbest]

    return nbest_hyps


def time_sync_decoding(decoder, h, recog_args, rnnlm=None):
    """Time synchronous beam search implementation.

    Based on https://ieeexplore.ieee.org/document/9053040

    Args:
        decoder (class): decoder class
        h (torch.Tensor): encoder hidden state sequences (Tmax, Henc)
        recog_args (Namespace): argument Namespace containing options
        rnnlm (torch.nn.Module): language module

    Returns:
        nbest_hyps (list of dicts): n-best decoding results

    """
    beam = recog_args.beam_size
    w_range = min(beam, decoder.odim)

    max_sym_exp = recog_args.nstep
    nbest = recog_args.nbest

    init_tensor = h.unsqueeze(0)
    state = decoder.zero_state(init_tensor)

    B = [
        {
            "yseq": [decoder.blank],
            "score": 0.0,
            "dec_state": state,
            "att_w": None,
            "lm_state": None,
        }
    ]

    for hi in h:
        A = []
        C = B

        for v in range(max_sym_exp):
            D = []

            for hyp in C:
                y, state, att_w, lm_tokens = decoder.forward_one_step(hyp, init_tensor)

                ytu = F.log_softmax(decoder.joint(hi, y[0]), dim=0)

                seq_A = [h["yseq"] for h in A]

                if hyp["yseq"] not in seq_A:
                    new_hyp = {
                        "score": hyp["score"] + float(ytu[0]),
                        "yseq": hyp["yseq"][:],
                        "dec_state": hyp["dec_state"],
                        "att_w": hyp["att_w"],
                        "lm_state": hyp["lm_state"],
                    }

                    A.append(new_hyp)
                else:
                    dict_pos = seq_A.index(hyp["yseq"])

                    A[dict_pos]["score"] = np.logaddexp(
                        A[dict_pos]["score"], (hyp["score"] + float(ytu[0]))
                    )

                if rnnlm:
                    rnnlm_state, rnnlm_scores = rnnlm.predict(
                        hyp["lm_state"], lm_tokens
                    )

                if v < max_sym_exp:
                    for k in range(1, decoder.odim):
                        beam_hyp = {
                            "score": hyp["score"] + float(ytu[k]),
                            "yseq": hyp["yseq"][:],
                            "dec_state": state,
                            "att_w": att_w if att_w is None else att_w[:],
                            "lm_state": hyp["lm_state"],
                        }

                        beam_hyp["yseq"].append(int(k))

                        if rnnlm:
                            beam_hyp["lm_state"] = rnnlm_state

                            beam_hyp["score"] += (
                                recog_args.lm_weight * rnnlm_scores[0][k]
                            )

                        D.append(beam_hyp)

            C = sorted(D, key=lambda x: x["score"], reverse=True)[:w_range]

        B = sorted(A, key=lambda x: x["score"], reverse=True)[:w_range]

    nbest_hyps = sorted(B, key=lambda x: x["score"], reverse=True)[:nbest]

    return nbest_hyps


def align_length_sync_decoding(decoder, h, recog_args, rnnlm=None):
    """Alignment-length synchronous beam search implementation.

    Based on https://ieeexplore.ieee.org/document/9053040

    Args:
        decoder (class): decoder class
        h (torch.Tensor): encoder hidden state sequences (Tmax, Henc)
        recog_args (Namespace): argument Namespace containing options
        rnnlm (torch.nn.Module): language module

    Returns:
        nbest_hyps (list of dicts): n-best decoding results

    """
    beam = recog_args.beam_size
    w_range = min(beam, decoder.odim)

    h_length = int(h.size(0))
    u_max = min(recog_args.u_max, (h_length - 1))

    nbest = recog_args.nbest

    init_tensor = h.unsqueeze(0)
    state = decoder.zero_state(init_tensor)

    B = [
        {
            "yseq": [decoder.blank],
            "score": 0.0,
            "dec_state": state,
            "att_w": None,
            "lm_state": None,
        }
    ]

    final = []

    for i in range(h_length + u_max):
        A = []

        for hyp in B:
            u = len(hyp["yseq"]) - 1
            t = i - u + 1

            if t > (h_length - 1):
                continue

            y, state, att_w, lm_tokens = decoder.forward_one_step(hyp, init_tensor)

            ytu = F.log_softmax(decoder.joint(h[t], y[0]), dim=0)

            new_hyp = {
                "score": hyp["score"] + float(ytu[0]),
                "yseq": hyp["yseq"][:],
                "dec_state": hyp["dec_state"],
                "att_w": hyp["att_w"],
                "lm_state": hyp["lm_state"],
            }

            A.append(new_hyp)

            if t == (h_length - 1):
                final.append(new_hyp)

            if rnnlm:
                rnnlm_state, rnnlm_scores = rnnlm.predict(hyp["lm_state"], lm_tokens)

            for k in range(1, decoder.odim):
                beam_hyp = {
                    "score": hyp["score"] + float(ytu[k]),
                    "yseq": hyp["yseq"][:],
                    "dec_state": state,
                    "att_w": att_w if att_w is None else att_w[:],
                    "lm_state": hyp["lm_state"],
                }

                beam_hyp["yseq"].append(int(k))

                if rnnlm:
                    beam_hyp["lm_state"] = rnnlm_state
                    beam_hyp["score"] += recog_args.lm_weight * rnnlm_scores[0][k]

                A.append(beam_hyp)

        B = sorted(A, key=lambda x: x["score"], reverse=True)[:w_range]
        B = recombine_hyps(B)

    if final:
        nbest_hyps = sorted(final, key=lambda x: x["score"], reverse=True)[:nbest]
    else:
        nbest_hyps = B[:nbest]

    return nbest_hyps


def nsc_beam_search(decoder, h, recog_args, rnnlm=None):
    """N-step constrained beam search implementation.

    Based and modified from https://arxiv.org/pdf/2002.03577.pdf

    Args:
        decoder (class): decoder class
        h (torch.Tensor): encoder hidden state sequences (Tmax, Henc)
        recog_args (Namespace): argument Namespace containing options
        rnnlm (torch.nn.Module): language module

    Returns:
        nbest_hyps (list of dicts): n-best decoding results

    """
    beam = recog_args.beam_size
    w_range = min(beam, decoder.odim)

    nstep = recog_args.nstep
    prefix_alpha = recog_args.prefix_alpha

    nbest = recog_args.nbest

    w_state = decoder.zero_state(torch.zeros((w_range, decoder.dunits)))

    init_tokens = [{"yseq": [decoder.blank]} for _ in range(w_range)]

    att_params = ([h.size(0)] * w_range, h.unsqueeze(0).expand(w_range, -1, -1))

    w_y, w_state, w_att_w, w_lm_tokens = decoder.forward_batch_one_step(
        init_tokens, w_state, None, att_params
    )

    state, att_w = decoder.get_idx_dec_state(w_state, 0, w_att_w)

    if rnnlm:
        w_lm_states, w_lm_scores = rnnlm.buff_predict(None, w_lm_tokens, w_range)

        if hasattr(rnnlm.predictor, "wordlm"):
            lm_type = "wordlm"
            lm_layers = len(w_lm_states[0])
        else:
            lm_type = "lm"
            lm_layers = len(w_lm_states["c"])

        lm_state = get_idx_lm_state(w_lm_states, 0, lm_type, lm_layers)
        lm_scores = w_lm_scores[0]
    else:
        lm_state = None
        lm_scores = None

    kept_hyps = [
        {
            "yseq": [decoder.blank],
            "score": 0.0,
            "dec_state": state,
            "att_w": att_w,
            "y": [w_y[0]],
            "lm_state": lm_state,
            "lm_scores": lm_scores,
        }
    ]

    for hi in h:
        hyps = sorted(kept_hyps, key=lambda x: len(x["yseq"]), reverse=True)
        kept_hyps = []

        for j in range(len(hyps) - 1):
            for i in range((j + 1), len(hyps)):
                if (
                    is_prefix(hyps[j]["yseq"], hyps[i]["yseq"])
                    and (len(hyps[j]["yseq"]) - len(hyps[i]["yseq"])) <= prefix_alpha
                ):
                    next_id = len(hyps[i]["yseq"])

                    ytu = F.log_softmax(decoder.joint(hi, hyps[i]["y"][-1]), dim=0)

                    curr_score = float(hyps[i]["score"]) + float(
                        ytu[hyps[j]["yseq"][next_id]]
                    )

                    for k in range(next_id, (len(hyps[j]["yseq"]) - 1)):
                        ytu = F.log_softmax(decoder.joint(hi, hyps[j]["y"][k]), dim=0)

                        curr_score += float(ytu[hyps[j]["yseq"][k + 1]])

                    hyps[j]["score"] = np.logaddexp(float(hyps[j]["score"]), curr_score)

        S = []
        V = []
        for n in range(nstep):
            h_enc = hi.unsqueeze(0).expand(w_range, -1)

            w_y = torch.stack([hyp["y"][-1] for hyp in hyps])

            if len(hyps) == 1:
                w_y = w_y.expand(w_range, -1)

            w_logprobs = F.log_softmax(decoder.joint(h_enc, w_y), dim=-1).view(-1)

            if rnnlm:
                w_lm_scores = torch.stack([hyp["lm_scores"] for hyp in hyps])

                if len(hyps) == 1:
                    w_lm_scores = w_lm_scores.expand(w_range, -1)

                w_lm_scores = w_lm_scores.contiguous().view(-1)

            for i, hyp in enumerate(hyps):
                pos_k = i * decoder.odim
                k_i = w_logprobs.narrow(0, pos_k, decoder.odim)

                if rnnlm:
                    lm_k_i = w_lm_scores.narrow(0, pos_k, decoder.odim)

                for k in range(decoder.odim):
                    curr_score = float(k_i[k])

                    w_hyp = {
                        "yseq": hyp["yseq"][:],
                        "score": hyp["score"] + curr_score,
                        "dec_state": hyp["dec_state"],
                        "att_w": hyp["att_w"],
                        "y": hyp["y"][:],
                        "lm_state": hyp["lm_state"],
                        "lm_scores": hyp["lm_scores"],
                    }

                    if k == decoder.blank:
                        S.append(w_hyp)
                    else:
                        w_hyp["yseq"].append(int(k))

                        if rnnlm:
                            w_hyp["score"] += recog_args.lm_weight * lm_k_i[k]

                        V.append(w_hyp)

            V = sorted(V, key=lambda x: x["score"], reverse=True)
            V = substract(V, hyps)[:w_range]

            w_state, w_att_w = decoder.get_batch_dec_states(w_state, V)
            w_y, w_state, w_att_w, w_lm_tokens = decoder.forward_batch_one_step(
                V, w_state, w_att_w, att_params
            )

            if rnnlm:
                w_lm_states = get_batch_lm_states(
                    [v["lm_state"] for v in V], lm_type, lm_layers
                )
                w_lm_states, w_lm_scores = rnnlm.buff_predict(
                    w_lm_states, w_lm_tokens, w_range
                )

            if n < (nstep - 1):
                for i, v in enumerate(V):
                    v["dec_state"], v["att_w"] = decoder.get_idx_dec_state(
                        w_state, i, w_att_w
                    )

                    v["y"].append(w_y[i])

                    if rnnlm:
                        v["lm_state"] = get_idx_lm_state(
                            w_lm_states, i, lm_type, lm_layers
                        )
                        v["lm_scores"] = w_lm_scores[i]

                hyps = V[:]
            else:
                w_logprobs = F.log_softmax(decoder.joint(h_enc, w_y), dim=-1).view(-1)
                blank_score = w_logprobs[0 :: decoder.odim]

                for i, v in enumerate(V):
                    if nstep != 1:
                        v["score"] += float(blank_score[i])

                    v["dec_state"], v["att_w"] = decoder.get_idx_dec_state(
                        w_state, i, w_att_w
                    )

                    v["y"].append(w_y[i])

                    if rnnlm:
                        v["lm_state"] = get_idx_lm_state(
                            w_lm_states, i, lm_type, lm_layers
                        )
                        v["lm_scores"] = w_lm_scores[i]

        kept_hyps = sorted((S + V), key=lambda x: x["score"], reverse=True)[:w_range]

    nbest_hyps = sorted(
        kept_hyps, key=lambda x: x["score"] / len(x["yseq"]), reverse=True
    )[:nbest]

    return nbest_hyps


def search_interface(decoder, h, recog_args, rnnlm):
    """Select and run search algorithms.

    Args:
        decoder (class): decoder class
        h (torch.Tensor): encoder hidden state sequences (Tmax, Henc)
        recog_args (Namespace): argument Namespace containing options
        rnnlm (torch.nn.Module): language module

    Returns:
        nbest_hyps (list of dicts): n-best decoding results

    """
    if hasattr(decoder, "att"):
        decoder.att[0].reset()

    if recog_args.beam_size == 1:
        nbest_hyps = greedy_search(decoder, h, recog_args)
    elif recog_args.search_type == "default":
        nbest_hyps = default_beam_search(decoder, h, recog_args, rnnlm)
    elif recog_args.search_type == "nsc":
        nbest_hyps = nsc_beam_search(decoder, h, recog_args, rnnlm)
    elif recog_args.search_type == "tsd":
        nbest_hyps = time_sync_decoding(decoder, h, recog_args, rnnlm)
    elif recog_args.search_type == "alsd":
        nbest_hyps = align_length_sync_decoding(decoder, h, recog_args, rnnlm)
    else:
        raise NotImplementedError

    return nbest_hyps
