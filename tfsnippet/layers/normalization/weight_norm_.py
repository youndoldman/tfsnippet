import tensorflow as tf
from tensorflow.contrib.framework import add_arg_scope

from tfsnippet.utils import (ParamSpec,
                             int_shape,
                             add_name_and_scope_arg_doc,
                             validate_int_or_int_tuple_arg,
                             resolve_negative_axis)

__all__ = ['weight_norm']


@add_arg_scope
@add_name_and_scope_arg_doc
def weight_norm(kernel,
                axis,
                use_scale=True,
                scale=None,
                scale_initializer=None,
                scale_regularizer=None,
                scale_constraint=None,
                trainable=True,
                epsilon=1e-12,
                name=None,
                scope=None):
    """
    Weight normalization proposed by (Salimans & Kingma, 2016).

    Roughly speaking, the weight normalization is defined as::

        kernel = scale * kernel / tf.reduce_mean(
            kernel, axis=<dimensions not in `axis`>, keepdims=True)

    This function does not support data-dependent initialization for `scale`.
    If you do need this feature, you have to turn off `scale`, and use
    :func:`~tfsnippet.layers.act_norm` along with :func:`weight_norm`.

    Args:
        kernel: Tensor, the weight `w` to be normalized.
        axis (tuple[int]): The axis to apply weight normalization (See above).
        use_scale (bool): Whether or not to use `scale`.  Default :obj:`True`.
        scale (Tensor): Instead of creating a new variable, use this tensor.
        scale_initializer: The initializer for `scale`.
        scale_regularizer: The regularizer for `scale`.
        scale_constraint: The constraint for `scale`.
        trainable (bool): Whether or not `log_scale` and `scale` are trainable?
        epsilon: Small float number to avoid dividing by zero.
    """
    # check the parameters
    if not use_scale and scale is not None:
        raise ValueError('`use_scale` is False but `scale` is specified.')

    kernel = tf.convert_to_tensor(kernel)
    kernel_shape = int_shape(kernel)
    dtype = kernel.dtype.base_dtype
    var_spec = ParamSpec(kernel_shape, dtype=dtype)

    if scale_initializer is None:
        scale_initializer = tf.ones_initializer(dtype=dtype)
    if scale is not None:
        scale = var_spec.validate(scale)

    # any dimension not specified in `axis` should be averaged out
    axis = resolve_negative_axis(
        len(kernel_shape), validate_int_or_int_tuple_arg('axis', axis))
    reduce_axis = tuple(a for a in range(len(kernel_shape)) if a not in axis)

    with tf.variable_scope(scope, default_name=name or 'weight_norm'):
        # normalize the kernel
        kernel = tf.nn.l2_normalize(kernel, axis=reduce_axis, epsilon=epsilon)

        # create the scaling variable
        if use_scale:
            if scale is None:
                scale = tf.get_variable(
                    'scale',
                    shape=kernel_shape,
                    dtype=dtype,
                    initializer=scale_initializer,
                    regularizer=scale_regularizer,
                    constraint=scale_constraint,
                    trainable=trainable
                )
            kernel = kernel * scale

        # now return the normalized weight
        return kernel
