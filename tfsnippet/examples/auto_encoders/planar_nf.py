# -*- coding: utf-8 -*-
import functools

import click
import tensorflow as tf
from tensorflow.contrib.framework import arg_scope, add_arg_scope

import tfsnippet as sn
from tfsnippet.examples.utils import (MLConfig,
                                      MLResults,
                                      save_images_collection,
                                      global_config as config,
                                      config_options,
                                      bernoulli_as_pixel,
                                      bernoulli_flow,
                                      print_with_title)


class ExpConfig(MLConfig):
    # model parameters
    z_dim = 40
    x_dim = 784
    nf_layers = 20

    # training parameters
    write_summary = False
    max_epoch = 3000
    max_step = None
    batch_size = 128
    l2_reg = 0.0001
    initial_lr = 0.001
    lr_anneal_factor = 0.5
    lr_anneal_epoch_freq = 300
    lr_anneal_step_freq = None

    # evaluation parameters
    test_n_z = 500
    test_batch_size = 128


@sn.global_reuse
@add_arg_scope
def q_net(x, posterior_flow, observed=None, n_z=None, is_training=True):
    net = sn.BayesianNet(observed=observed)

    # compute the hidden features
    with arg_scope([sn.layers.dense],
                   activation_fn=tf.nn.leaky_relu,
                   kernel_regularizer=sn.layers.l2_regularizer(config.l2_reg)):
        h_x = tf.to_float(x)
        h_x = sn.layers.dense(h_x, 500)
        h_x = sn.layers.dense(h_x, 500)

    # sample z ~ q(z|x)
    z_mean = sn.layers.dense(h_x, config.z_dim, name='z_mean')
    z_logstd = sn.layers.dense(h_x, config.z_dim, name='z_logstd')
    z = net.add('z', sn.Normal(mean=z_mean, logstd=z_logstd), n_samples=n_z,
                group_ndims=1, flow=posterior_flow)

    return net


@sn.global_reuse
@add_arg_scope
def p_net(observed=None, n_z=None, is_training=True):
    net = sn.BayesianNet(observed=observed)

    # sample z ~ p(z)
    z = net.add('z', sn.Normal(mean=tf.zeros([1, config.z_dim]),
                               logstd=tf.zeros([1, config.z_dim])),
                group_ndims=1, n_samples=n_z)

    # compute the hidden features
    with arg_scope([sn.layers.dense],
                   activation_fn=tf.nn.leaky_relu,
                   kernel_regularizer=sn.layers.l2_regularizer(config.l2_reg)):
        h_z = z
        h_z = sn.layers.dense(h_z, 500)
        h_z = sn.layers.dense(h_z, 500)

    # sample x ~ p(x|z)
    x_logits = sn.layers.dense(h_z, config.x_dim, name='x_logits')
    x = net.add('x', sn.Bernoulli(logits=x_logits), group_ndims=1)

    return net


@click.command()
@click.option('--result-dir', help='The result directory.', metavar='PATH',
              required=False, type=str)
@config_options(ExpConfig)
def main(result_dir):
    # print the config
    print_with_title('Configurations', config.format_config(), after='\n')

    # open the result object and prepare for result directories
    results = MLResults(result_dir)
    results.make_dirs('plotting', exist_ok=True)
    results.make_dirs('train_summary', exist_ok=True)

    # input placeholders
    input_x = tf.placeholder(
        dtype=tf.int32, shape=(None, config.x_dim), name='input_x')
    is_training = tf.placeholder(
        dtype=tf.bool, shape=(), name='is_training')
    learning_rate = tf.placeholder(shape=(), dtype=tf.float32)
    learning_rate_var = sn.AnnealingDynamicValue(config.initial_lr,
                                                 config.lr_anneal_factor)

    # build the model
    with arg_scope([q_net, p_net], is_training=is_training):
        # build the posterior flow
        posterior_flow = sn.layers.planar_normalizing_flows(
            config.nf_layers, name='posterior_flow')

        # derive the loss and lower-bound for training
        with tf.name_scope('training'):
            train_q_net = q_net(input_x, posterior_flow)
            train_chain = train_q_net.chain(
                p_net, latent_axis=0, observed={'x': input_x})

            vae_loss = tf.reduce_mean(train_chain.vi.training.sgvb())
            loss = vae_loss + tf.losses.get_regularization_loss()

        # derive the nll and logits output for testing
        with tf.name_scope('testing'):
            test_q_net = q_net(input_x, posterior_flow, n_z=config.test_n_z)
            test_chain = test_q_net.chain(
                p_net, latent_axis=0, observed={'x': input_x})
            test_nll = -tf.reduce_mean(
                test_chain.vi.evaluation.is_loglikelihood())
            test_lb = tf.reduce_mean(test_chain.vi.lower_bound.elbo())

    # derive the optimizer
    with tf.name_scope('optimizing'):
        optimizer = tf.train.AdamOptimizer(learning_rate)
        params = tf.trainable_variables()
        grads = optimizer.compute_gradients(loss, var_list=params)
        with tf.control_dependencies(
                tf.get_collection(tf.GraphKeys.UPDATE_OPS)):
            train_op = optimizer.apply_gradients(grads)

    # derive the plotting function
    with tf.name_scope('plotting'):
        plot_p_net = p_net(n_z=100, is_training=is_training)
        x_plots = tf.reshape(bernoulli_as_pixel(plot_p_net['x']), (-1, 28, 28))

    def plot_samples(loop):
        with loop.timeit('plot_time'):
            images = session.run(x_plots, feed_dict={is_training: False})
            save_images_collection(
                images=images,
                filename='plotting/{}.png'.format(loop.epoch),
                grid_size=(10, 10)
            )

    # prepare for training and testing data
    (x_train, y_train), (x_test, y_test) = sn.datasets.load_mnist()
    train_flow = bernoulli_flow(
        x_train, config.batch_size, shuffle=True, skip_incomplete=True)
    test_flow = bernoulli_flow(
        x_test, config.test_batch_size, sample_now=True)

    with sn.utils.create_session().as_default() as session, \
            train_flow.threaded(5) as train_flow:
        # train the network
        with sn.TrainLoop(params,
                          var_groups=['p_net', 'q_net', 'posterior_flow'],
                          max_epoch=config.max_epoch,
                          max_step=config.max_step,
                          summary_dir=(results.system_path('train_summary')
                                       if config.write_summary else None),
                          summary_graph=tf.get_default_graph(),
                          early_stopping=False) as loop:
            trainer = sn.Trainer(
                loop, train_op, [input_x], train_flow,
                feed_dict={learning_rate: learning_rate_var, is_training: True},
                metrics={'loss': loss}
            )
            trainer.anneal_after(
                learning_rate_var,
                epochs=config.lr_anneal_epoch_freq,
                steps=config.lr_anneal_step_freq
            )
            evaluator = sn.Evaluator(
                loop,
                metrics={'test_nll': test_nll, 'test_lb': test_lb},
                inputs=[input_x],
                data_flow=test_flow,
                feed_dict={is_training: False},
                time_metric_name='test_time'
            )
            evaluator.after_run.add_hook(
                lambda: results.update_metrics(evaluator.last_metrics_dict))
            trainer.evaluate_after_epochs(evaluator, freq=10)
            trainer.evaluate_after_epochs(
                functools.partial(plot_samples, loop), freq=10)
            trainer.log_after_epochs(freq=1)
            trainer.run()

    # print the final metrics and close the results object
    print_with_title('Results', results.format_metrics(), before='\n')
    results.close()


if __name__ == '__main__':
    main()
