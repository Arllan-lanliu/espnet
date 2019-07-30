# encoding: utf-8
import collections
import logging
import math
import numpy as np
import six

# chainer related
import chainer

from chainer import cuda
from chainer import training
from chainer import Variable
from chainer import functions as F

from chainer.training.updaters.multiprocess_parallel_updater import gather_grads
from chainer.training.updaters.multiprocess_parallel_updater import gather_params
from chainer.training.updaters.multiprocess_parallel_updater import scatter_grads

from chainer.training import extension
from cupy.cuda import nccl
import timeit


# copied from https://github.com/chainer/chainer/blob/master/chainer/optimizer.py
def sum_sqnorm(arr):
    sq_sum = collections.defaultdict(float)
    for x in arr:
        with cuda.get_device_from_array(x) as dev:
            if x is not None:
                x = x.ravel()
                s = x.dot(x)
                sq_sum[int(dev)] += s
    return sum([float(i) for i in six.itervalues(sq_sum)])


class CustomUpdater(training.StandardUpdater):
    """Custom updater for chainer"""

    def __init__(self, train_iter, optimizer, converter, device, accum_grad=1):
        super(CustomUpdater, self).__init__(
            train_iter, optimizer, converter=converter, device=device)
        self.count = 0
        self.accum_grad = accum_grad
        logging.info('using custom converter for transformer')

    # The core part of the update routine can be customized by overriding.
    def update_core(self):
        # When we pass one iterator and optimizer to StandardUpdater.__init__,
        # they are automatically named 'main'.
        _start = timeit.default_timer()
        self.count += 1
        train_iter = self.get_iterator('main')
        optimizer = self.get_optimizer('main')

        # Get batch and convert into variables
        batch = train_iter.next()
        x = self.converter(batch, self.device)
        if self.count == 1:
            optimizer.target.cleargrads()

        # Compute the loss at this time step and accumulate it
        loss = optimizer.target(*x) / self.accum_grad
        loss.backward()  # Backprop

        logging.info(timeit.default_timer() - _start)
        _start = timeit.default_timer()
        # compute the gradient norm to check if it is normal or not
        grad_norm = np.sqrt(sum_sqnorm(
            [p.grad for p in optimizer.target.params(False)]))
        logging.info('grad norm={}'.format(grad_norm))
        if math.isnan(grad_norm):
            logging.warning('grad norm is nan. Do not update model.')
            optimizer.target.cleargrads()  # Clear the parameter gradients
        elif self.count % self.accum_grad == 0:
            optimizer.update()
            optimizer.target.cleargrads()  # Clear the parameter gradients
        logging.info(timeit.default_timer() - _start)


class CustomParallelUpdater(training.updaters.MultiprocessParallelUpdater):
    """Custom parallel updater for chainer"""

    def __init__(self, train_iters, optimizer, converter, devices, accum_grad=1):
        super(CustomParallelUpdater, self).__init__(
            train_iters, optimizer, converter=converter, devices=devices)
        self.count = 0
        self.accum_grad = accum_grad
        logging.info('using custom parallel updater for transformer')

    # The core part of the update routine can be customized by overriding.
    def update_core(self):
        self.count += 1
        self.setup_workers()

        self._send_message(('update', None))
        with cuda.Device(self._devices[0]):
            
            # For reducing memory
            optimizer = self.get_optimizer('main')
            batch = self.get_iterator('main').next()
            x = self.converter(batch, self._devices[0])

            loss = self._master(*x) / self.accum_grad
            loss.backward()

            # NCCL: reduce grads
            null_stream = cuda.Stream.null
            if self.comm is not None:
                gg = gather_grads(self._master)
                self.comm.reduce(gg.data.ptr, gg.data.ptr, gg.size,
                                 nccl.NCCL_FLOAT,
                                 nccl.NCCL_SUM,
                                 0, null_stream.ptr)
                scatter_grads(self._master, gg)
                del gg

            # check gradient value
            grad_norm = np.sqrt(sum_sqnorm(
                [p.grad for p in optimizer.target.params(False)]))
            logging.info('grad norm={}'.format(grad_norm))

            # update
            if math.isnan(grad_norm):
                logging.warning('grad norm is nan. Do not update model.')
            elif self.count % self.accum_grad == 0:
                optimizer.update()
                self._master.cleargrads()

            if self.comm is not None:
                gp = gather_params(self._master)
                self.comm.bcast(gp.data.ptr, gp.size, nccl.NCCL_FLOAT,
                                0, null_stream.ptr)


class VaswaniRule(extension.Extension):

    """Trainer extension to shift an optimizer attribute magically by Vaswani.

    Args:
        attr (str): Name of the attribute to shift.
        rate (float): Rate of the exponential shift. This value is multiplied
            to the attribute at each call.
        init (float): Initial value of the attribute. If it is ``None``, the
            extension extracts the attribute at the first call and uses it as
            the initial value.
        target (float): Target value of the attribute. If the attribute reaches
            this value, the shift stops.
        optimizer (~chainer.Optimizer): Target optimizer to adjust the
            attribute. If it is ``None``, the main optimizer of the updater is
            used.

    """

    def __init__(self, attr, d, warmup_steps=4000,
                 init=None, target=None, optimizer=None,
                 scale=1.):
        self._attr = attr
        self._d_inv05 = d ** (-0.5) * scale
        self._warmup_steps_inv15 = warmup_steps ** (-1.5)
        self._init = init
        self._target = target
        self._optimizer = optimizer
        self._t = 0
        self._last_value = None

    def initialize(self, trainer):
        optimizer = self._get_optimizer(trainer)
        # ensure that _init is set
        if self._init is None:
            self._init = self._d_inv05 * (1. * self._warmup_steps_inv15)
        if self._last_value is not None:  # resuming from a snapshot
            self._update_value(optimizer, self._last_value)
        else:
            self._update_value(optimizer, self._init)

    def __call__(self, trainer):
        self._t += 1
        optimizer = self._get_optimizer(trainer)
        value = self._d_inv05 * \
            min(self._t ** (-0.5), self._t * self._warmup_steps_inv15)
        self._update_value(optimizer, value)

    def serialize(self, serializer):
        self._t = serializer('_t', self._t)
        self._last_value = serializer('_last_value', self._last_value)

    def _get_optimizer(self, trainer):
        return self._optimizer or trainer.updater.get_optimizer('main')

    def _update_value(self, optimizer, value):
        setattr(optimizer, self._attr, value)
        self._last_value = value


class CustomConverter(object):
    """Custom Converter

    :param
    """

    def __init__(self, subsampling_factor=1):
        pass

    def __call__(self, batch, device):
        # For transformer, data is processed in CPU.
        # batch should be located in list
        assert len(batch) == 1
        xs, ys = batch[0]
        xs = F.pad_sequence(xs, padding=-1).data
        # get batch of lengths of input sequences
        ilens = np.array([x.shape[0] for x in xs], dtype=np.int32)
        return xs, ilens, ys