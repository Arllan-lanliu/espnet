from collections import OrderedDict
import copy
import importlib
import io
import json
import logging

import h5py
import kaldiio
import numpy as np
import soundfile


def dynamic_import(import_path):
    alias = dict(
        delta='espnet.transform.add_deltas:AddDeltas',
        cmvn='espnet.transform.cmvn:CMVN',
        utterance_cmvn='espnet.transform.cmvn:UtteranceCMVN',
        fbank='espnet.transform.spectrogram:LogMelSpectrogram',
        spectrogram='espnet.transform.spectrogram:Spectrogram',
        stft='espnet.transform.spectrogram:Stft',
        wpe='espnet.transform.wpe:WPE')

    if import_path not in alias and ':' not in import_path:
        raise ValueError(
            'import_path should be one of {} or '
            'include ":", e.g. "espnet.transform.add_deltas:AddDeltas" : '
            '{}'.format(set(alias), import_path))
    if ':' not in import_path:
        import_path = alias[import_path]

    module_name, objname = import_path.split(':')
    m = importlib.import_module(module_name)
    return getattr(m, objname)


class Preprocessing(object):
    """Apply some functions to the mini-batch

    Examples:
        >>> kwargs = {"process": [{"type": "fbank",
        ...                        "n_mels": 80,
        ...                        "fs": 16000},
        ...                       {"type": "cmvn",
        ...                        "stats": "data/train/cmvn.ark",
        ...                        "norm_vars": True},
        ...                       {"type": "delta", "window": 2, "order": 2}],
        ...           "mode": "sequential"}
        >>> preprocessing = Preprocessing(**kwargs)
        >>> bs = 10
        >>> xs = [np.random.randn(100, 80).astype(np.float32)
        ...       for _ in range(bs)]
        >>> processed_xs = preprocessing(xs)
    """

    def __init__(self, conf=None, **kwargs):
        if conf is not None:
            with io.open(conf, encoding='utf-8') as f:
                conf = json.load(f)
                assert isinstance(conf, dict), type(conf)
            conf.update(kwargs)
            kwargs = conf

        if len(kwargs) == 0:
            self.conf = {'mode': 'sequential', 'process': []}
        else:
            # Deep-copy to avoid sharing of mutable objects
            self.conf = copy.deepcopy(kwargs)

        self.functions = OrderedDict()
        if self.conf.get('mode', 'sequential') == 'sequential':
            for idx, process in enumerate(self.conf['process']):
                assert isinstance(process, dict), type(process)
                opts = dict(process)
                process_type = opts.pop('type')
                class_obj = dynamic_import(process_type)
                self.functions[idx] = class_obj(**opts)
        else:
            raise NotImplementedError(
                'Not supporting mode={}'.format(self.conf['mode']))

    def __repr__(self):
        rep = '\n' + '\n'.join(
            '    {}: {}'.format(k, v) for k, v in self.functions.items())
        return '{}({})'.format(self.__class__.__name__, rep)

    def __call__(self, xs):
        """Return new mini-batch

        :param List[np.ndarray] xs:
        :return: batch:
        :rtype: List[np.ndarray]
        """
        if not isinstance(xs, (list, tuple)):
            is_batch = False
            xs = [xs]
        else:
            is_batch = True

        if self.conf.get('mode', 'sequential') == 'sequential':
            for idx in range(len(self.conf['process'])):
                func = self.functions[idx]
                try:
                    xs = [func(x) for x in xs]
                except Exception:
                    logging.fatal('Catch a exception from {}th func: {}'
                                  .format(idx, func))
                    raise
        else:
            raise NotImplementedError(
                'Not supporting mode={}'.format(self.conf['mode']))

        if is_batch:
            return xs
        else:
            return xs[0]


class LoadInputsAndTargets(object):
    """Create a mini-batch from a list of dicts

    :param: str mode: Specify the task mode, "asr" or "tts"
    :param: str preproces_conf: The path of a json file for pre-processing
    :param: bool load_input: If False, not to load the input data
    :param: bool load_output: If False, not to load the output data
    :param: bool sort_in_input_length: Sort the mini-batch in descending order
        of the input length
    :param: bool use_speaker_embedding: Used for tts mode only
    :param: bool use_second_target: Used for tts mode only
    """

    def __init__(self, mode='asr',
                 preprocess_conf=None,
                 load_input=True,
                 load_output=True,
                 sort_in_input_length=True,
                 use_speaker_embedding=False,
                 use_second_target=False
                 ):
        self._loaders = {}
        if mode not in ['asr', 'tts']:
            raise ValueError(
                'Only asr or tts are allowed: mode={}'.format(mode))
        if preprocess_conf is not None:
            self.preprocessing = Preprocessing(preprocess_conf)
            logging.warning(
                '[Experimental feature] Some pre-transform will be done '
                'for the mini-batch creation using {}'
                .format(self.preprocessing))
        else:
            # If conf doesn't exist, this function don't touch anything.
            self.preprocessing = None

        if use_second_target and use_speaker_embedding and mode == 'tts':
            raise ValueError('Choose one of "use_second_target" and '
                             '"use_speaker_embedding "')
        if (use_second_target or use_speaker_embedding) and mode != 'tts':
            logging.warning(
                '"use_second_target" and "use_speaker_embedding" is '
                'used only for tts mode')

        self.mode = mode
        self.load_output = load_output
        self.load_input = load_input
        self.sort_in_input_length = sort_in_input_length
        self.use_speaker_embedding = use_speaker_embedding
        self.use_second_target = use_second_target

    def __call__(self, batch):
        """Function to load inputs and targets from list of dicts

        :param List[Tuple[str, dict]] batch: list of dict which is subset of
            loaded data.json
        :return: list of input token id sequences [(L_1), (L_2), ..., (L_B)]
        :return: list of input feature sequences
            [(T_1, D), (T_2, D), ..., (T_B, D)]
        :rtype: list of float ndarray
        :return: list of target token id sequences [(L_1), (L_2), ..., (L_B)]
        :rtype: list of int ndarray
        """
        x_feats_dict = OrderedDict()  # OrderedDict[str, List[np.ndarray]]
        y_feats_dict = OrderedDict()  # OrderedDict[str, List[np.ndarray]]

        if self.load_input:
            for uttid, info in batch:
                for idx, inp in enumerate(info['input']):
                    if 'filetype' not in inp:
                        # ======= Legacy format for input =======
                        # {"input": [{"feat": "some/path.ark:123"}]),
                        x = kaldiio.load_mat(inp['feat'])
                    else:
                        # ======= New format =======
                        # {"input":
                        #  [{"feat": "some/path.h5:F01_050C0101_PED_REAL",
                        #    "filetype": "hdf5",

                        x = self._get_from_loader(
                            file_path=inp['feat'], loader_type=inp['filetype'])
                    x_feats_dict.setdefault(inp['name'], []).append(x)

        # FIXME(kamo): Dirty way to load only speaker_embedding without the other inputs
        if not self.load_input and \
                self.mode == 'tts' and self.use_speaker_embedding:
            for uttid, info in batch:
                for idx, inp in enumerate(info['input']):
                    if idx != 1:
                        x = None
                    else:
                        if 'filetype' not in inp:
                            x = kaldiio.load_mat(inp['feat'])
                        else:
                            x = self._get_from_loader(
                                file_path=inp['feat'],
                                loader_type=inp['filetype'])
                    x_feats_dict.setdefault(inp['name'], []).append(x)

        if self.load_output:
            for uttid, info in batch:
                for idx, inp in enumerate(info['output']):
                    if 'tokenid' in inp:
                        # ======= Legacy format for output =======
                        # {"output": [{"tokenid": "1 2 3 4"}])
                        assert isinstance(inp['tokenid'], str), \
                            type(inp['tokenid'])
                        x = np.fromiter(map(int, inp['tokenid'].split()),
                                        dtype=np.int64)
                    else:
                        # ======= New format =======
                        # {"input":
                        #  [{"feat": "some/path.h5:F01_050C0101_PED_REAL",
                        #    "filetype": "hdf5",

                        x = self._get_from_loader(
                            file_path=inp['feat'], loader_type=inp['filetype'])

                    y_feats_dict.setdefault(inp['name'], []).append(x)

        if self.mode == 'asr':
            return_batch = self._create_batch_asr(x_feats_dict, y_feats_dict)

        elif self.mode == 'tts':
            eos = int(batch[0][1]['output'][0]['shape'][1]) - 1
            return_batch = self._create_batch_tts(x_feats_dict, y_feats_dict,
                                                  eos)
        else:
            raise NotImplementedError

        if self.preprocessing is not None:
            # Apply pre-processing only to input1 feature, now
            if 'input1' in return_batch:
                return_batch['input1'] = \
                    self.preprocessing(return_batch['input1'])

        # Doesn't return the names now.
        return tuple(return_batch.values())

    def _create_batch_asr(self, x_feats_dict, y_feats_dict):
        """Create a OrderedDict for the mini-batch

        :param OrderedDict x_feats_dict:
        :param OrderedDict y_feats_dict:
        :return:
        :rtype: OrderedDict
        """
        # Create a list from the first item
        xs = list(x_feats_dict.values())[0]

        if self.load_output:
            ys = list(y_feats_dict.values())[0]
            assert len(xs) == len(ys), (len(xs), len(ys))

            # get index of non-zero length samples
            nonzero_idx = filter(lambda i: len(ys[i]) > 0, range(len(ys)))
        else:
            nonzero_idx = range(len(xs))

        if self.sort_in_input_length:
            # sort in input lengths
            nonzero_sorted_idx = sorted(nonzero_idx, key=lambda i: -len(xs[i]))
        else:
            nonzero_sorted_idx = nonzero_idx

        if len(nonzero_sorted_idx) != len(xs):
            logging.warning(
                'Target sequences include empty tokenid (batch %d -> %d).' % (
                    len(xs), len(nonzero_sorted_idx)))

        # remove zero-length samples
        xs = [xs[i] for i in nonzero_sorted_idx]
        x_name = list(x_feats_dict.keys())[0]
        if self.load_output:
            ys = [ys[i] for i in nonzero_sorted_idx]
            y_name = list(y_feats_dict.keys())[0]

            return_batch = OrderedDict([(x_name, xs), (y_name, ys)])
        else:
            return_batch = OrderedDict([(x_name, xs)])
        return return_batch

    def _create_batch_tts(self, x_feats_dict, y_feats_dict, eos):
        """Create a OrderedDict for the mini-batch

        :param OrderedDict x_feats_dict:
        :param OrderedDict y_feats_dict:
        :param int eos:
        :return:
        :rtype: OrderedDict
        """
        # Use the output values as the input feats for tts mode
        xs = list(y_feats_dict.values())[0]
        # get index of non-zero length samples
        nonzero_idx = filter(lambda i: len(xs[i]) > 0, range(len(xs)))
        # sort in input lengths
        if self.sort_in_input_length:
            # sort in input lengths
            nonzero_sorted_idx = sorted(nonzero_idx, key=lambda i: -len(xs[i]))
        else:
            nonzero_sorted_idx = nonzero_idx
        # remove zero-length samples
        xs = [xs[i] for i in nonzero_sorted_idx]
        # Added eos into input sequence
        xs = [np.append(x, eos) for x in xs]

        if self.load_input:
            ys = list(x_feats_dict.values())[0]
            assert len(xs) == len(ys), (len(xs), len(ys))
            ys = [ys[i] for i in nonzero_sorted_idx]

            spembs = None
            spcs = None
            spembs_name = 'spembs_none'
            spcs_name = 'spcs_none'

            if self.use_second_target:
                spcs = list(x_feats_dict.values())[1]
                spcs = [spcs[i] for i in nonzero_sorted_idx]
                spcs_name = list(x_feats_dict.keys())[1]

            if self.use_speaker_embedding:
                spembs = list(x_feats_dict.values())[1]
                spembs = [spembs[i] for i in nonzero_sorted_idx]
                spembs_name = list(x_feats_dict.keys())[1]

            x_name = list(y_feats_dict.keys())[0]
            y_name = list(x_feats_dict.keys())[0]

            return_batch = OrderedDict([(x_name, xs),
                                        (y_name, ys),
                                        (spembs_name, spembs),
                                        (spcs_name, spcs)])
        elif self.use_speaker_embedding:
            spembs = list(x_feats_dict.values())[1]
            spembs = [spembs[i] for i in nonzero_sorted_idx]

            x_name = list(y_feats_dict.keys())[0]
            spembs_name = list(x_feats_dict.keys())[1]

            return_batch = OrderedDict([(x_name, xs),
                                        (spembs_name, spembs)])
        else:
            x_name = list(y_feats_dict.keys())[0]

            return_batch = OrderedDict([(x_name, xs)])
        return return_batch

    def _get_from_loader(self, file_path, loader_type):
        """Return ndarray

        In order to make the fds to be opened only at the first referring,
        the loader are stored in self._loaders

        :param: str file_path:
        :param: str loader_type:
        :return:
        :rtype: np.ndarray
        """
        if loader_type == 'hdf5':
            file_path, key = file_path.split(':', 1)
            loader = self._loaders.get(file_path)
            if loader is None:
                #    {"input": [{"feat": "some/path.h5:F01_050C0101_PED_REAL",
                #                "filetype": "hdf5",
                loader = h5py.File(file_path, 'r')
                self._loaders[file_path] = loader
            return loader[key][...]
        elif loader_type == 'flac.hdf5':
            file_path, key = file_path.split(':', 1)
            loader = self._loaders.get(file_path)
            if loader is None:
                #    {"input": [{"feat": "some/path.h5:F01_050C0101_PED_REAL",
                #                "filetype": "flac.hdf5",
                loader = SoundHDF5File(file_path, 'r',
                                       format='flac', dtype='int16')
                self._loaders[file_path] = loader
            array, rate = loader[key]
            return array
        elif loader_type == 'soundfile':
            # Assume PCM16
            array, rate = soundfile.read(file_path, dtype='int16')
            return array
        elif loader_type == 'npz':
            file_path, key = file_path.split(':', 1)
            loader = self._loaders.get(file_path)
            if loader is None:
                #    {"input": [{"feat": "some/path.npz:F01_050C0101_PED_REAL",
                #                "filetype": "npz",
                loader = np.load(file_path)
                self._loaders[file_path] = loader
            return loader[key]
        elif loader_type == 'npy':
            #    {"input": [{"feat": "some/path.npy",
            #                "filetype": "npy"},
            return np.load(file_path)
        elif loader_type in ['mat', 'vec']:
            #    {"input": [{"feat": "some/path.ark:123",
            #                "filetype": "mat"}]},
            # load_mat can load both matrix and vector
            return kaldiio.load_mat(file_path)
        elif loader_type == 'scp':
            file_path, key = file_path.split(':', 1)
            loader = self._loaders.get(file_path)
            if loader is None:
                #    {"input": [{"feat": "some/path.scp:F01_050C0101_PED_REAL",
                #                "filetype": "scp",
                loader = kaldiio.load_scp(file_path)
            return loader[key]
        else:
            raise NotImplementedError(
                'Not supported: loader_type={}'.format(loader_type))


class SoundHDF5File(object):
    """Collecting sound files to a HDF5 file

    >>> f = SoundHDF5File('a.flac.h5', mode='a', format='flac')
    >>> array = np.random.randint(0, 100, 100, dtype=np.int16)
    >>> f['id'] = (array, 16000)
    >>> array, rate = f['id']

    """

    def __init__(self, filepath, mode='r', format='flac', dtype='int16',
                 **kwargs):
        self.file = h5py.File(filepath, mode, **kwargs)
        self.format = format
        self.dtype = dtype

    def create_dataset(self, name, shape=None, data=None, **kwds):
        f = io.BytesIO()
        array, rate = data
        assert array.dtype == np.dtype(self.dtype), array.dtype
        soundfile.write(f, array, rate, format=self.format)
        self.file.create_dataset(name, shape=shape,
                                 data=np.void(f.getvalue()), **kwds)

    def __setitem__(self, name, data):
        self.create_dataset(name, data=data)

    def __getitem__(self, key):
        data = self.file[key][...]
        f = io.BytesIO(data.tobytes())
        array, rate = soundfile.read(f, format=self.format, dtype=self.dtype)
        return array, rate

    def keys(self):
        return self.file.keys()

    def values(self):
        return self.file.values()

    def items(self):
        return self.file.items()

    def __iter__(self):
        return iter(self.file)

    def __contains__(self, item):
        return item in self.file

    def __len__(self, item):
        return len(self.file)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.file.close()

    def close(self):
        self.file.close()
