import logging
import random
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Collection, Dict, Iterable, List, Union

import numpy as np
import pyopenjtalk
import scipy.signal
import soundfile
from typeguard import check_argument_types, check_return_type

from espnet2.text.build_tokenizer import build_tokenizer
from espnet2.text.cleaner import TextCleaner
from espnet2.text.token_id_converter import TokenIDConverter


class AbsPreprocessor(ABC):
    def __init__(self, train: bool):
        self.train = train

    @abstractmethod
    def __call__(
        self, uid: str, data: Dict[str, Union[str, np.ndarray]]
    ) -> Dict[str, np.ndarray]:
        raise NotImplementedError


def framing(
    x,
    frame_length: int = 512,
    frame_shift: int = 256,
    centered: bool = True,
    padded: bool = True,
):
    if x.size == 0:
        raise ValueError("Input array size is zero")
    if frame_length < 1:
        raise ValueError("frame_length must be a positive integer")
    if frame_length > x.shape[-1]:
        raise ValueError("frame_length is greater than input length")
    if 0 >= frame_shift:
        raise ValueError("frame_shift must be greater than 0")

    if centered:
        pad_shape = [(0, 0) for _ in range(x.ndim - 1)] + [
            (frame_length // 2, frame_length // 2)
        ]
        x = np.pad(x, pad_shape, mode="constant", constant_values=0)

    if padded:
        # Pad to integer number of windowed segments
        # I.e make x.shape[-1] = frame_length + (nseg-1)*nstep,
        #  with integer nseg
        nadd = (-(x.shape[-1] - frame_length) % frame_shift) % frame_length
        pad_shape = [(0, 0) for _ in range(x.ndim - 1)] + [(0, nadd)]
        x = np.pad(x, pad_shape, mode="constant", constant_values=0)

    # Created strided array of data segments
    if frame_length == 1 and frame_length == frame_shift:
        result = x[..., None]
    else:
        shape = x.shape[:-1] + (
            (x.shape[-1] - frame_length) // frame_shift + 1,
            frame_length,
        )
        strides = x.strides[:-1] + (frame_shift * x.strides[-1], x.strides[-1])
        result = np.lib.stride_tricks.as_strided(x, shape=shape, strides=strides)
    return result


def detect_non_silence(
    x: np.ndarray,
    threshold: float = 0.01,
    frame_length: int = 1024,
    frame_shift: int = 512,
    window: str = "boxcar",
) -> np.ndarray:
    """Power based voice activity detection.

    Args:
        x: (Channel, Time)
    >>> x = np.random.randn(1000)
    >>> detect = detect_non_silence(x)
    >>> assert x.shape == detect.shape
    >>> assert detect.dtype == np.bool
    """
    if x.shape[-1] < frame_length:
        return np.full(x.shape, fill_value=True, dtype=np.bool)

    if x.dtype.kind == "i":
        x = x.astype(np.float64)
    # framed_w: (C, T, F)
    framed_w = framing(
        x,
        frame_length=frame_length,
        frame_shift=frame_shift,
        centered=False,
        padded=True,
    )
    framed_w *= scipy.signal.get_window(window, frame_length).astype(framed_w.dtype)
    # power: (C, T)
    power = (framed_w**2).mean(axis=-1)
    # mean_power: (C, 1)
    mean_power = np.mean(power, axis=-1, keepdims=True)
    if np.all(mean_power == 0):
        return np.full(x.shape, fill_value=True, dtype=np.bool)
    # detect_frames: (C, T)
    detect_frames = power / mean_power > threshold
    # detects: (C, T, F)
    detects = np.broadcast_to(
        detect_frames[..., None], detect_frames.shape + (frame_shift,)
    )
    # detects: (C, TF)
    detects = detects.reshape(*detect_frames.shape[:-1], -1)
    # detects: (C, TF)
    return np.pad(
        detects,
        [(0, 0)] * (x.ndim - 1) + [(0, x.shape[-1] - detects.shape[-1])],
        mode="edge",
    )


class CommonPreprocessor(AbsPreprocessor):
    def __init__(
        self,
        train: bool,
        token_type: str = None,
        token_list: Union[Path, str, Iterable[str]] = None,
        bpemodel: Union[Path, str, Iterable[str]] = None,
        text_cleaner: Collection[str] = None,
        g2p_type: str = None,
        unk_symbol: str = "<unk>",
        space_symbol: str = "<space>",
        non_linguistic_symbols: Union[Path, str, Iterable[str]] = None,
        delimiter: str = None,
        rir_scp: str = None,
        rir_apply_prob: float = 1.0,
        noise_scp: str = None,
        noise_apply_prob: float = 1.0,
        noise_db_range: str = "3_10",
        short_noise_thres: float = 0.5,
        speech_volume_normalize: float = None,
        speech_name: str = "speech",
        text_name: str = "text",
        fs: np.int32 = 0,
    ):
        super().__init__(train)
        self.train = train
        self.speech_name = speech_name
        self.text_name = text_name
        self.speech_volume_normalize = speech_volume_normalize
        self.rir_apply_prob = rir_apply_prob
        self.noise_apply_prob = noise_apply_prob
        self.short_noise_thres = short_noise_thres

        if token_type is not None:
            if token_list is None:
                raise ValueError("token_list is required if token_type is not None")
            self.text_cleaner = TextCleaner(text_cleaner)

            self.tokenizer = build_tokenizer(
                token_type=token_type,
                bpemodel=bpemodel,
                delimiter=delimiter,
                space_symbol=space_symbol,
                non_linguistic_symbols=non_linguistic_symbols,
                g2p_type=g2p_type,
            )
            self.token_id_converter = TokenIDConverter(
                token_list=token_list,
                unk_symbol=unk_symbol,
            )
        else:
            self.text_cleaner = None
            self.tokenizer = None
            self.token_id_converter = None

        if train and rir_scp is not None:
            self.rirs = []
            with open(rir_scp, "r", encoding="utf-8") as f:
                for line in f:
                    sps = line.strip().split(None, 1)
                    if len(sps) == 1:
                        self.rirs.append(sps[0])
                    else:
                        self.rirs.append(sps[1])
        else:
            self.rirs = None

        if train and noise_scp is not None:
            self.noises = []
            with open(noise_scp, "r", encoding="utf-8") as f:
                for line in f:
                    sps = line.strip().split(None, 1)
                    if len(sps) == 1:
                        self.noises.append(sps[0])
                    else:
                        self.noises.append(sps[1])
            sps = noise_db_range.split("_")
            if len(sps) == 1:
                self.noise_db_low, self.noise_db_high = float(sps[0])
            elif len(sps) == 2:
                self.noise_db_low, self.noise_db_high = float(sps[0]), float(sps[1])
            else:
                raise ValueError(
                    "Format error: '{noise_db_range}' e.g. -3_4 -> [-3db,4db]"
                )
        else:
            self.noises = None

    def _convolve_rir(self, speech, power):
        rir_path = np.random.choice(self.rirs)
        rir = None
        if rir_path is not None:
            rir, _ = soundfile.read(rir_path, dtype=np.float64, always_2d=True)

            # rir: (Nmic, Time)
            rir = rir.T

            # speech: (Nmic, Time)
            # Note that this operation doesn't change the signal length
            speech = scipy.signal.convolve(speech, rir, mode="full")[
                :, : speech.shape[1]
            ]
            # Reverse mean power to the original power
            power2 = (speech[detect_non_silence(speech)] ** 2).mean()
            speech = np.sqrt(power / max(power2, 1e-10)) * speech
        return speech, rir

    def _add_noise(self, speech, power):
        nsamples = speech.shape[1]
        noise_path = np.random.choice(self.noises)
        noise = None
        if noise_path is not None:
            noise_db = np.random.uniform(self.noise_db_low, self.noise_db_high)
            with soundfile.SoundFile(noise_path) as f:
                if f.frames == nsamples:
                    noise = f.read(dtype=np.float64, always_2d=True)
                elif f.frames < nsamples:
                    if f.frames / nsamples < self.short_noise_thres:
                        logging.warning(
                            f"Noise ({f.frames}) is much shorter than "
                            f"speech ({nsamples}) in dynamic mixing"
                        )
                    offset = np.random.randint(0, nsamples - f.frames)
                    # noise: (Time, Nmic)
                    noise = f.read(dtype=np.float64, always_2d=True)
                    # Repeat noise
                    noise = np.pad(
                        noise,
                        [(offset, nsamples - f.frames - offset), (0, 0)],
                        mode="wrap",
                    )
                else:
                    offset = np.random.randint(0, f.frames - nsamples)
                    f.seek(offset)
                    # noise: (Time, Nmic)
                    noise = f.read(nsamples, dtype=np.float64, always_2d=True)
                    if len(noise) != nsamples:
                        raise RuntimeError(f"Something wrong: {noise_path}")
            # noise: (Nmic, Time)
            noise = noise.T

            noise_power = (noise**2).mean()
            scale = (
                10 ** (-noise_db / 20)
                * np.sqrt(power)
                / np.sqrt(max(noise_power, 1e-10))
            )
            speech = speech + scale * noise
        return speech, noise

    def _speech_process(
        self, data: Dict[str, Union[str, np.ndarray]]
    ) -> Dict[str, Union[str, np.ndarray]]:
        assert check_argument_types()
        if self.speech_name in data:
            if self.train and (self.rirs is not None or self.noises is not None):
                speech = data[self.speech_name]

                # speech: (Nmic, Time)
                if speech.ndim == 1:
                    speech = speech[None, :]
                else:
                    speech = speech.T
                # Calc power on non silence region
                power = (speech[detect_non_silence(speech)] ** 2).mean()

                # 1. Convolve RIR
                if self.rirs is not None and self.rir_apply_prob >= np.random.random():
                    speech, _ = self._convolve_rir(speech, power)

                # 2. Add Noise
                if (
                    self.noises is not None
                    and self.noise_apply_prob >= np.random.random()
                ):
                    speech, _ = self._add_noise(speech, power)

                speech = speech.T
                ma = np.max(np.abs(speech))
                if ma > 1.0:
                    speech /= ma
                data[self.speech_name] = speech

            if self.speech_volume_normalize is not None:
                speech = data[self.speech_name]
                ma = np.max(np.abs(speech))
                data[self.speech_name] = speech * self.speech_volume_normalize / ma
        assert check_return_type(data)
        return data

    def _text_process(
        self, data: Dict[str, Union[str, np.ndarray]]
    ) -> Dict[str, np.ndarray]:
        if self.text_name in data and self.tokenizer is not None:
            text = data[self.text_name]
            text = self.text_cleaner(text)
            tokens = self.tokenizer.text2tokens(text)
            text_ints = self.token_id_converter.tokens2ids(tokens)
            data[self.text_name] = np.array(text_ints, dtype=np.int64)
        assert check_return_type(data)
        return data

    def __call__(
        self, uid: str, data: Dict[str, Union[str, np.ndarray]]
    ) -> Dict[str, np.ndarray]:
        assert check_argument_types()

        data = self._speech_process(data)
        data = self._text_process(data)
        return data


class SLUPreprocessor(CommonPreprocessor):
    def __init__(
        self,
        train: bool,
        token_type: str = None,
        token_list: Union[Path, str, Iterable[str]] = None,
        transcript_token_list: Union[Path, str, Iterable[str]] = None,
        bpemodel: Union[Path, str, Iterable[str]] = None,
        text_cleaner: Collection[str] = None,
        g2p_type: str = None,
        unk_symbol: str = "<unk>",
        space_symbol: str = "<space>",
        non_linguistic_symbols: Union[Path, str, Iterable[str]] = None,
        delimiter: str = None,
        rir_scp: str = None,
        rir_apply_prob: float = 1.0,
        noise_scp: str = None,
        noise_apply_prob: float = 1.0,
        noise_db_range: str = "3_10",
        short_noise_thres: float = 0.5,
        speech_volume_normalize: float = None,
        speech_name: str = "speech",
        text_name: str = "text",
    ):
        super().__init__(
            train=train,
            token_type=token_type,
            token_list=token_list,
            bpemodel=bpemodel,
            text_cleaner=text_cleaner,
            g2p_type=g2p_type,
            unk_symbol=unk_symbol,
            space_symbol=space_symbol,
            non_linguistic_symbols=non_linguistic_symbols,
            delimiter=delimiter,
            rir_scp=rir_scp,
            rir_apply_prob=rir_apply_prob,
            noise_scp=noise_scp,
            noise_apply_prob=noise_apply_prob,
            noise_db_range=noise_db_range,
            short_noise_thres=short_noise_thres,
            speech_volume_normalize=speech_volume_normalize,
            speech_name=speech_name,
            text_name=text_name,
        )
        if transcript_token_list is not None:
            print("using transcript")
            self.transcript_tokenizer = build_tokenizer(
                token_type="word",
                bpemodel=bpemodel,
                delimiter=delimiter,
                space_symbol=space_symbol,
                non_linguistic_symbols=non_linguistic_symbols,
                g2p_type=g2p_type,
            )
            self.transcript_token_id_converter = TokenIDConverter(
                token_list=transcript_token_list,
                unk_symbol=unk_symbol,
            )
        else:
            self.transcript_tokenizer = None
            self.transcript_token_id_converter = None

    def _text_process(
        self, data: Dict[str, Union[str, np.ndarray]]
    ) -> Dict[str, np.ndarray]:
        if self.text_name in data and self.tokenizer is not None:
            text = data[self.text_name]
            text = self.text_cleaner(text)
            tokens = self.tokenizer.text2tokens(text)
            text_ints = self.token_id_converter.tokens2ids(tokens)
            data[self.text_name] = np.array(text_ints, dtype=np.int64)
        if "transcript" in data and self.tokenizer is not None:
            text = data["transcript"]
            text = self.text_cleaner(text)
            tokens = self.transcript_tokenizer.text2tokens(text)
            text_ints = self.transcript_token_id_converter.tokens2ids(tokens)
            data["transcript"] = np.array(text_ints, dtype=np.int64)
        assert check_return_type(data)
        return data


class CommonPreprocessor_multi(AbsPreprocessor):
    def __init__(
        self,
        train: bool,
        token_type: str = None,
        token_list: Union[Path, str, Iterable[str]] = None,
        bpemodel: Union[Path, str, Iterable[str]] = None,
        text_cleaner: Collection[str] = None,
        g2p_type: str = None,
        unk_symbol: str = "<unk>",
        space_symbol: str = "<space>",
        non_linguistic_symbols: Union[Path, str, Iterable[str]] = None,
        delimiter: str = None,
        speech_name: str = "speech",
        text_name: List[str] = ["text"],
    ):
        super().__init__(train)
        self.train = train
        self.speech_name = speech_name
        self.text_name = text_name

        if token_type is not None:
            if token_list is None:
                raise ValueError("token_list is required if token_type is not None")
            self.text_cleaner = TextCleaner(text_cleaner)

            self.tokenizer = build_tokenizer(
                token_type=token_type,
                bpemodel=bpemodel,
                delimiter=delimiter,
                space_symbol=space_symbol,
                non_linguistic_symbols=non_linguistic_symbols,
                g2p_type=g2p_type,
            )
            self.token_id_converter = TokenIDConverter(
                token_list=token_list,
                unk_symbol=unk_symbol,
            )
        else:
            self.text_cleaner = None
            self.tokenizer = None
            self.token_id_converter = None

    def _text_process(
        self, data: Dict[str, Union[str, np.ndarray]]
    ) -> Dict[str, np.ndarray]:
        for text_n in self.text_name:
            if text_n in data and self.tokenizer is not None:
                text = data[text_n]
                text = self.text_cleaner(text)
                tokens = self.tokenizer.text2tokens(text)
                text_ints = self.token_id_converter.tokens2ids(tokens)
                data[text_n] = np.array(text_ints, dtype=np.int64)
        assert check_return_type(data)
        return data

    def __call__(
        self, uid: str, data: Dict[str, Union[str, np.ndarray]]
    ) -> Dict[str, np.ndarray]:
        assert check_argument_types()

        if self.speech_name in data:
            # Nothing now: candidates:
            # - STFT
            # - Fbank
            # - CMVN
            # - Data augmentation
            pass

        data = self._text_process(data)
        return data


class MutliTokenizerCommonPreprocessor(CommonPreprocessor):
    def __init__(
        self,
        train: bool,
        token_type: List[str] = [None],
        token_list: List[Union[Path, str, Iterable[str]]] = [None],
        bpemodel: List[Union[Path, str, Iterable[str]]] = [None],
        text_cleaner: Collection[str] = None,
        g2p_type: str = None,
        unk_symbol: str = "<unk>",
        space_symbol: str = "<space>",
        non_linguistic_symbols: Union[Path, str, Iterable[str]] = None,
        delimiter: str = None,
        rir_scp: str = None,
        rir_apply_prob: float = 1.0,
        noise_scp: str = None,
        noise_apply_prob: float = 1.0,
        noise_db_range: str = "3_10",
        short_noise_thres: float = 0.5,
        speech_volume_normalize: float = None,
        speech_name: str = "speech",
        text_name: List[str] = ["text"],
    ):
        # TODO(jiatong): sync with Kamo and Jing on interface for preprocessor
        super().__init__(
            train=train,
            token_type=token_type[0],
            token_list=token_list[0],
            bpemodel=bpemodel[0],
            text_cleaner=text_cleaner,
            g2p_type=g2p_type,
            unk_symbol=unk_symbol,
            space_symbol=space_symbol,
            non_linguistic_symbols=non_linguistic_symbols,
            delimiter=delimiter,
            speech_name=speech_name,
            text_name=text_name[0],
            rir_scp=rir_scp,
            rir_apply_prob=rir_apply_prob,
            noise_scp=noise_scp,
            noise_apply_prob=noise_apply_prob,
            noise_db_range=noise_db_range,
            short_noise_thres=short_noise_thres,
            speech_volume_normalize=speech_volume_normalize,
        )

        assert (
            len(token_type) == len(token_list) == len(bpemodel) == len(text_name)
        ), "token_type, token_list, bpemodel, or processing text_name mismatched"
        self.num_tokenizer = len(token_type)
        self.tokenizer = []
        self.token_id_converter = []

        for i in range(self.num_tokenizer):
            if token_type[i] is not None:
                if token_list[i] is None:
                    raise ValueError("token_list is required if token_type is not None")

                self.tokenizer.append(
                    build_tokenizer(
                        token_type=token_type[i],
                        bpemodel=bpemodel[i],
                        delimiter=delimiter,
                        space_symbol=space_symbol,
                        non_linguistic_symbols=non_linguistic_symbols,
                        g2p_type=g2p_type,
                    )
                )
                self.token_id_converter.append(
                    TokenIDConverter(
                        token_list=token_list[i],
                        unk_symbol=unk_symbol,
                    )
                )
            else:
                self.tokenizer.append(None)
                self.token_id_converter.append(None)

        self.text_cleaner = TextCleaner(text_cleaner)
        self.text_name = text_name  # override the text_name from CommonPreprocessor

    def _text_process(
        self, data: Dict[str, Union[str, np.ndarray]]
    ) -> Dict[str, np.ndarray]:
        for i in range(self.num_tokenizer):
            text_name = self.text_name[i]
            if text_name in data and self.tokenizer[i] is not None:
                text = data[text_name]
                text = self.text_cleaner(text)
                tokens = self.tokenizer[i].text2tokens(text)
                text_ints = self.token_id_converter[i].tokens2ids(tokens)
                data[text_name] = np.array(text_ints, dtype=np.int64)
        assert check_return_type(data)
        return data


class DynamicMixingPreprocessor(AbsPreprocessor):
    def __init__(
        self,
        train: bool,
        source_scp: str = None,
        ref_num: int = 2,
        dynamic_mixing_gain_db: float = 0.0,
        speech_name: str = "speech_mix",
        speech_ref_name_prefix: str = "speech_ref",
        mixture_source_name: str = None,
        utt2spk: str = None,
    ):

        super().__init__(train)
        self.source_scp = source_scp
        self.ref_num = ref_num
        self.dynamic_mixing_gain_db = dynamic_mixing_gain_db
        self.speech_name = speech_name
        self.speech_ref_name_prefix = speech_ref_name_prefix
        # mixture_source_name: the key to select source utterances from dataloader
        if mixture_source_name is None:
            self.mixture_source_name = f"{speech_ref_name_prefix}1"
        else:
            self.mixture_source_name = mixture_source_name

        self.sources = {}
        assert (
            source_scp is not None
        ), f"Please pass `source_scp` to {type(self).__name__}"
        with open(source_scp, "r", encoding="utf-8") as f:
            for line in f:
                sps = line.strip().split(None, 1)
                assert len(sps) == 2
                self.sources[sps[0]] = sps[1]

        self.utt2spk = {}
        if utt2spk is None:
            # if utt2spk is not provided, create a dummy utt2spk with uid.
            for key in self.sources.keys():
                self.utt2spk[key] = key
        else:
            with open(utt2spk, "r", encoding="utf-8") as f:
                for line in f:
                    sps = line.strip().split(None, 1)
                    assert len(sps) == 2
                    self.utt2spk[sps[0]] = sps[1]

            for key in self.sources.keys():
                assert key in self.utt2spk

        self.source_keys = list(self.sources.keys())

    def _pick_source_utterances_(self, uid):
        # return (ref_num - 1) uid of reference sources.

        source_keys = [uid]

        spk_ids = [self.utt2spk[uid]]

        retry_cnt = 0
        while len(source_keys) < self.ref_num:
            picked = random.choice(self.source_keys)
            spk_id = self.utt2spk[picked]

            # make one utterance or one speaker only appears once in mixing.
            if (picked not in source_keys) and (spk_id not in spk_ids):
                source_keys.append(picked)
            else:
                retry_cnt += 1
                if retry_cnt > 10:
                    source_keys.append(picked)
                    logging.warning(
                        "Can not find speech source from different speaker "
                        f"for {retry_cnt} times."
                        "There may be problems with training data. "
                        "Please check the utt2spk file."
                    )

        return source_keys[1:]

    def _read_source_(self, key, speech_length):

        source, _ = soundfile.read(
            self.sources[key],
            dtype=np.float32,
            always_2d=False,
        )

        if speech_length > source.shape[0]:
            pad = speech_length - source.shape[0]
            source = np.pad(source, (0, pad), "reflect")
        else:
            source = source[0:speech_length]

        assert speech_length == source.shape[0]

        return source

    def _mix_speech_(self, uid, data):

        # pick sources
        source_keys = self._pick_source_utterances_(uid)

        # load audios
        speech_length = data[self.mixture_source_name].shape[0]
        ref_audios = [self._read_source_(key, speech_length) for key in source_keys]
        ref_audios = [data[self.mixture_source_name]] + ref_audios

        # apply random gain to speech sources

        gain_in_db = [
            random.uniform(-self.dynamic_mixing_gain_db, self.dynamic_mixing_gain_db)
            for i in range(len(ref_audios))
        ]
        gain = [10 ** (g_db / 20.0) for g_db in gain_in_db]

        ref_audios = [ref * g for ref, g in zip(ref_audios, gain)]

        speech_mix = np.sum(np.array(ref_audios), axis=0)

        for i, ref in enumerate(ref_audios):
            data[f"{self.speech_ref_name_prefix}{i+1}"] = ref
        data[self.speech_name] = speech_mix

        return data

    def __call__(
        self, uid: str, data: Dict[str, Union[str, np.ndarray]]
    ) -> Dict[str, np.ndarray]:

        # TODO(Chenda): need to test for multi-channel data.
        assert (
            len(data[self.mixture_source_name].shape) == 1
        ), "Multi-channel input has not been tested"

        if self.train:
            data = self._mix_speech_(uid, data)

        assert check_return_type(data)
        return data


class EnhPreprocessor(CommonPreprocessor):
    """Preprocessor for Speech Enhancement (Enh) task."""

    def __init__(
        self,
        train: bool,
        rir_scp: str = None,
        rir_apply_prob: float = 1.0,
        noise_scp: str = None,
        noise_apply_prob: float = 1.0,
        noise_db_range: str = "3_10",
        short_noise_thres: float = 0.5,
        speech_volume_normalize: float = None,
        speech_name: str = "speech_mix",
        speech_ref_name_prefix: str = "speech_ref",
        noise_ref_name_prefix: str = "noise_ref",
        dereverb_ref_name_prefix: str = "dereverb_ref",
        use_reverberant_ref: bool = False,
        num_spk: int = 1,
        num_noise_type: int = 1,
        sample_rate: int = 8000,
        force_single_channel: bool = False,
    ):
        super().__init__(
            train=train,
            token_type=None,
            token_list=None,
            bpemodel=None,
            text_cleaner=None,
            g2p_type=None,
            unk_symbol="<unk>",
            space_symbol="<space>",
            non_linguistic_symbols=None,
            delimiter=None,
            rir_scp=rir_scp,
            rir_apply_prob=rir_apply_prob,
            noise_scp=noise_scp,
            noise_apply_prob=noise_apply_prob,
            noise_db_range=noise_db_range,
            short_noise_thres=short_noise_thres,
            speech_volume_normalize=speech_volume_normalize,
            speech_name=speech_name,
        )
        self.speech_ref_name_prefix = speech_ref_name_prefix
        self.noise_ref_name_prefix = noise_ref_name_prefix
        self.dereverb_ref_name_prefix = dereverb_ref_name_prefix
        self.use_reverberant_ref = use_reverberant_ref
        self.num_spk = num_spk
        self.num_noise_type = num_noise_type
        self.sample_rate = sample_rate
        self.force_single_channel = force_single_channel

        if self.speech_volume_normalize is not None:
            sps = speech_volume_normalize.split("_")
            if len(sps) == 1:
                self.volume_low, self.volume_high = float(sps[0])
            elif len(sps) == 2:
                self.volume_low, self.volume_high = float(sps[0]), float(sps[1])
            else:
                raise ValueError(
                    "Format error for --speech_volume_normalize: "
                    f"'{speech_volume_normalize}'"
                )

    def _ensure_2d(self, signal):
        if isinstance(signal, tuple):
            return tuple(self._ensure_2d(sig) for sig in signal)
        elif isinstance(signal, list):
            return [self._ensure_2d(sig) for sig in signal]
        else:
            # (Nmic, Time)
            return signal[None, :] if signal.ndim == 1 else signal.T

    def _get_early_signal(self, speech, rir, power):
        predelay = 50  # milliseconds
        dt = np.argmax(rir, axis=1).min()
        et = dt + (predelay * self.sample_rate) // 1000
        rir_early = rir[:, :et]
        speech2 = scipy.signal.convolve(speech, rir_early, mode="full")[
            :, : speech.shape[1]
        ]
        # Reverse mean power to the original power
        power2 = (speech2[detect_non_silence(speech2)] ** 2).mean()
        speech2 = np.sqrt(power / max(power2, 1e-10)) * speech2
        return speech2

    def _apply_to_all_signals(self, data_dict, func):
        data_dict[self.speech_name] = func(data_dict[self.speech_name])

        for n in range(self.num_noise_type):
            noise_name = self.noise_ref_name_prefix + str(n + 1)
            if noise_name in data_dict:
                data_dict[noise_name] = func(data_dict[noise_name])

        for spk in range(self.num_spk):
            speech_ref_name = self.speech_ref_name_prefix + str(spk + 1)
            if self.train or speech_ref_name in data_dict:
                data_dict[speech_ref_name] = func(data_dict[speech_ref_name])

            dereverb_ref_name = self.dereverb_ref_name_prefix + str(spk + 1)
            if dereverb_ref_name in data_dict:
                data_dict[dereverb_ref_name] = func(data_dict[dereverb_ref_name])

    def _speech_process(
        self, data: Dict[str, Union[str, np.ndarray]]
    ) -> Dict[str, Union[str, np.ndarray]]:
        assert check_argument_types()

        if self.speech_name not in data:
            assert check_return_type(data)
            return data

        if self.train:
            # clean speech signal (Nmic, Time)
            speech_ref = [
                self._ensure_2d(data[self.speech_ref_name_prefix + str(i + 1)])
                for i in range(self.num_spk)
            ]

            # dereverberated (noisy) signal (Nmic, Time)
            if "dereverb_ref1" in data:
                dereverb_speech_ref = [
                    self._ensure_2d(data[self.dereverb_ref_name_prefix + str(i + 1)])
                    for i in range(self.num_spk)
                    if self.dereverb_ref_name_prefix + str(i + 1) in data
                ]
                assert len(dereverb_speech_ref) in (1, self.num_spk), len(
                    dereverb_speech_ref
                )
            else:
                dereverb_speech_ref = None

            # Calc power on non silence region
            power_ref = [
                (sref[detect_non_silence(sref)] ** 2).mean() for sref in speech_ref
            ]

            # 1. Convolve RIR
            if self.rirs is not None and self.rir_apply_prob >= np.random.random():
                speech_ref, rir_ref = zip(
                    *[
                        self._convolve_rir(sp, power)
                        for sp, power in zip(speech_ref, power_ref)
                    ]
                )
                if self.force_single_channel:
                    speech_ref = list(
                        map(lambda x: x if x.shape[0] == 1 else x[:1], speech_ref)
                    )
                    rir_ref = list(
                        map(lambda x: x if x.shape[0] == 1 else x[:1], rir_ref)
                    )

                if self.use_reverberant_ref:
                    for spk in range(self.num_spk):
                        suffix = str(spk + 1)
                        speech_ref_name = self.speech_ref_name_prefix + suffix
                        # (Time, Nmic)
                        data[speech_ref_name] = speech_ref[spk].T

                        if dereverb_speech_ref is not None:
                            if spk == 0 or len(dereverb_speech_ref) > 1:
                                dereverb_name = self.dereverb_ref_name_prefix + suffix
                                data[dereverb_name] = self._get_early_signal(
                                    speech_ref[spk], rir_ref[spk], power_ref[spk]
                                ).T
                else:
                    for spk in range(self.num_spk):
                        suffix = str(spk + 1)
                        speech_ref_name = self.speech_ref_name_prefix + suffix
                        # clean speech with early reflections (Time, Nmic)
                        data[speech_ref_name] = self._get_early_signal(
                            speech_ref[spk], rir_ref[spk], power_ref[spk]
                        ).T

                        if dereverb_speech_ref is not None:
                            if spk == 0 or len(dereverb_speech_ref) > 1:
                                dereverb_name = self.dereverb_ref_name_prefix + suffix
                                data[dereverb_name] = data[speech_ref_name]

            speech_mix = sum(speech_ref)
            power_mix = (speech_mix[detect_non_silence(speech_mix)] ** 2).mean()

            # 2. Add Noise
            if self.noises is not None and self.noise_apply_prob >= np.random.random():
                speech_mix, noise = self._add_noise(speech_mix, power_mix)
                if self.force_single_channel:
                    if speech_mix.shape[0] > 1:
                        speech_mix = speech_mix[:1]
                    if noise.shape[0] > 1:
                        noise = noise[:1]

                for n in range(1, self.num_noise_type):
                    name = self.noise_ref_name_prefix + str(n + 1)
                    data.pop(name, None)
                data[self.noise_ref_name_prefix + "1"] = noise.T

            speech_mix = speech_mix.T
            data[self.speech_name] = speech_mix
            ma = np.max(np.abs(speech_mix))
            if ma > 1.0:
                self._apply_to_all_signals(data, lambda x: x / ma)

            self._apply_to_all_signals(data, lambda x: x.squeeze())

        if self.force_single_channel:
            self._apply_to_all_signals(data, lambda x: x if x.ndim == 1 else x[:, 0])

        if self.speech_volume_normalize is not None:
            if self.train:
                volume_scale = np.random.uniform(self.volume_low, self.volume_high)
            else:
                # use a fixed scale to make it deterministic
                volume_scale = self.volume_low
            speech_mix = data[self.speech_name]
            ma = np.max(np.abs(speech_mix))
            self._apply_to_all_signals(data, lambda x: x * volume_scale / ma)

        assert check_return_type(data)
        return data


class SVSPreprocessor(AbsPreprocessor):
    """Preprocessor for Sing Voice Sythesis (SVS) task."""

    def __init__(
        self,
        train: bool,
        token_type: str = None,
        token_list: Union[Path, str, Iterable[str]] = None,
        bpemodel: Union[Path, str, Iterable[str]] = None,
        text_cleaner: Collection[str] = None,
        g2p_type: str = None,
        unk_symbol: str = "<unk>",
        space_symbol: str = "<space>",
        non_linguistic_symbols: Union[Path, str, Iterable[str]] = None,
        delimiter: str = None,
        singing_volume_normalize: float = None,
        singing_name: str = "singing",
        text_name: str = "text",
        label_name: str = "label",
        midi_name: str = "midi",
        fs: np.int32 = 0,
        align: list = [
            "singing",
            "label_lab",
            "midi_lab",
            "tempo_lab",
            "beat_lab",
        ],  # TODO(Tao): add to args
    ):
        super().__init__(train)
        self.train = train
        self.singing_name = singing_name
        self.text_name = text_name
        self.label_name = label_name
        self.midi_name = midi_name
        self.fs = fs
        self.singing_volume_normalize = singing_volume_normalize
        self.align = align
        if token_type is not None:
            if token_list is None:
                raise ValueError("token_list is required if token_type is not None")
            self.text_cleaner = TextCleaner(text_cleaner)

            self.tokenizer = build_tokenizer(
                token_type=token_type,
                bpemodel=bpemodel,
                delimiter=delimiter,
                space_symbol=space_symbol,
                non_linguistic_symbols=non_linguistic_symbols,
                g2p_type=g2p_type,
            )
            self.token_id_converter = TokenIDConverter(
                token_list=token_list,
                unk_symbol=unk_symbol,
            )
        else:
            self.text_cleaner = None
            self.tokenizer = None
            self.token_id_converter = None

    def __call__(
        self,
        uid: str,
        data: Dict[str, Union[str, np.ndarray, tuple]],
    ) -> Dict[str, np.ndarray]:
        assert check_argument_types()

        if self.singing_name in data:
            if self.singing_volume_normalize is not None:
                singing = data[self.singing_name]
                ma = np.max(np.abs(singing))
                data[self.singing_name] = singing * self.singing_volume_normalize / ma

        if (
            self.midi_name in data
            and self.label_name in data
            and self.tokenizer is not None
        ):
            # Load label info
            lab_timeseq, text = data[self.label_name]
            lab_len = len(text)
            text = " ".join(text)
            text = self.text_cleaner(text)
            tokens = self.tokenizer.text2tokens(text)
            text_ints = self.token_id_converter.tokens2ids(tokens)
            data.pop(self.label_name)

            # Load xml info
            # TODO(Yuning): It can only work on Japanese dataset now.
            # Syllable2phoneme mismatch and more languags settings need to be settled.
            syllables, notemidis, notetimeseq, tempo = data[self.midi_name]
            midis = []
            xml_timeseq = []
            phn_cnt = 0
            vowel = ["a", "i", "u", "e", "o"]
            for i in range(len(syllables)):
                # NOTE: Some phonemes are tagged differently
                if syllables[i] == "へ":
                    phn = ["h", "e"]
                else:
                    phn = pyopenjtalk.g2p(syllables[i]).split(" ")
                phn_num = len(phn)
                vowel_num = 0
                for p in phn:
                    if p in vowel:
                        vowel_num += 1
                if vowel_num == 2 and phn[0] != "k" and phn[0] != "g":
                    phn_num -= 1
                assert vowel_num <= 2
                phn_cnt += phn_num
                for _ in range(phn_num):
                    midis.append(notemidis[i])
                if phn_num == 1:
                    xml_timeseq.append([notetimeseq[i][0], notetimeseq[i][1]])
                elif phn_num == 2:
                    t = (
                        notetimeseq[i][0]
                        + (notetimeseq[i][1] - notetimeseq[i][0]) * 0.25
                    )
                    xml_timeseq.append([notetimeseq[i][0], t])
                    xml_timeseq.append([t, notetimeseq[i][1]])
                elif phn_num == 3:
                    t1 = (
                        notetimeseq[i][0]
                        + (notetimeseq[i][1] - notetimeseq[i][0]) * 0.1
                    )
                    t2 = (
                        notetimeseq[i][0]
                        + (notetimeseq[i][1] - notetimeseq[i][0]) * 0.5
                    )
                    xml_timeseq.append([notetimeseq[i][0], t1])
                    xml_timeseq.append([t1, t2])
                    xml_timeseq.append([t2, notetimeseq[i][1]])
                elif phn_num == 4:
                    t1 = (
                        notetimeseq[i][0]
                        + (notetimeseq[i][1] - notetimeseq[i][0]) * 0.05
                    )
                    t2 = (
                        notetimeseq[i][0]
                        + (notetimeseq[i][1] - notetimeseq[i][0]) * 0.1
                    )
                    t3 = (
                        notetimeseq[i][0]
                        + (notetimeseq[i][1] - notetimeseq[i][0]) * 0.5
                    )
                    xml_timeseq.append([notetimeseq[i][0], t1])
                    xml_timeseq.append([t1, t2])
                    xml_timeseq.append([t2, t3])
                    xml_timeseq.append([t3, notetimeseq[i][1]])
                else:
                    raise ValueError("Syllable to phoneme conversion error!")
            assert phn_cnt == lab_len
            data.pop(self.midi_name)

            # Calculate feature according to label time sequence
            timeseq = lab_timeseq
            nsamples = int((timeseq[-1][1] - timeseq[0][0]) * self.fs)

            labelseq_lab = np.zeros((nsamples))
            temposeq_lab = np.full(nsamples, tempo)
            beatseq_lab = np.zeros((nsamples))
            midiseq_lab = np.zeros((nsamples))
            offset = timeseq[0][0]
            for i in range(len(timeseq)):
                start = int((timeseq[i][0] - offset) * self.fs)
                end = int((timeseq[i][1] - offset) * self.fs) + 1
                if end > nsamples:
                    end = nsamples
                labelseq_lab[start:end] = text_ints[i]
                midiseq_lab[start:end] = midis[i]
                beat_num = (timeseq[i][1] - timeseq[i][0]) * tempo / 60
                beatseq_lab[start:end] = int(beat_num * tempo + 0.5)

            labelseq_lab.astype(np.int64)
            midiseq_lab.astype(np.int64)
            temposeq_lab.astype(np.int64)
            beatseq_lab.astype(np.int64)

            data["midi_lab"] = midiseq_lab
            data["tempo_lab"] = temposeq_lab
            data["beat_lab"] = beatseq_lab
            data["label_lab"] = labelseq_lab

            # Calculate feature according to XML time sequence
            timeseq = xml_timeseq
            nsamples = int((timeseq[-1][1] - timeseq[0][0]) * self.fs)

            labelseq_xml = np.zeros((nsamples))
            midiseq_xml = np.zeros((nsamples))
            temposeq_xml = np.full(nsamples, tempo)
            beatseq_xml = np.zeros((nsamples))
            offset = timeseq[0][0]
            for i in range(len(timeseq)):
                start = int((timeseq[i][0] - offset) * self.fs)
                end = int((timeseq[i][1] - offset) * self.fs) + 1
                if end > nsamples:
                    end = nsamples
                labelseq_xml[start:end] = text_ints[i]
                midiseq_xml[start:end] = midis[i]
                beat_num = (timeseq[i][1] - timeseq[i][0]) * tempo / 60
                beatseq_xml[start:end] = int(beat_num * tempo + 0.5)

            labelseq_xml.astype(np.int64)
            midiseq_xml.astype(np.int64)
            temposeq_xml.astype(np.int64)
            beatseq_xml.astype(np.int64)

            data["midi_xml"] = midiseq_xml
            data["tempo_xml"] = temposeq_xml
            data["label_xml"] = labelseq_xml
            data["beat_xml"] = beatseq_xml

        if self.text_name in data and self.tokenizer is not None:
            text = data[self.text_name]
            if not isinstance(text, np.ndarray):
                if not isinstance(text, str):
                    text = " ".join(text)
                text = self.text_cleaner(text)
                tokens = self.tokenizer.text2tokens(text)
                _text_ints = self.token_id_converter.tokens2ids(tokens)
                data[self.text_name] = np.array(_text_ints, dtype=np.int64)
        # TODO allow the tuple type

        # align frame length with singing
        length = min([len(data[key]) for key in data.keys() if key in self.align])
        for key in self.align:
            if key in data:
                data[key] = data[key][:length]

        return data
