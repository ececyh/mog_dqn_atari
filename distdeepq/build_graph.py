"""Deep Q learning graph

The functions in this file can are used to create the following functions:

======= act ========

    Function to chose an action given an observation

    Parameters
    ----------
    observation: object
        Observation that can be feed into the output of make_obs_ph
    stochastic: bool
        if set to False all the actions are always deterministic (default False)
    update_eps_ph: float
        update epsilon a new value, if negative not update happens
        (default: no update)

    Returns
    -------
    Tensor of dtype tf.int64 and shape (BATCH_SIZE,) with an action to be performed for
    every element of the batch.


======= train =======

    Function that takes a transition (s,a,r,s') and optimizes Bellman equation's error:

        td_error = Q(s,a) - (r + gamma * max_a' Q(s', a'))
        loss = huber_loss[td_error]

    Parameters
    ----------
    obs_t: object
        a batch of observations
    action: np.array
        actions that were selected upon seeing obs_t.
        dtype must be int32 and shape must be (batch_size,)
    reward: np.array
        immediate reward attained after executing those actions
        dtype must be float32 and shape must be (batch_size,)
    obs_tp1: object
        observations that followed obs_t
    done: np.array
        1 if obs_t was the last observation in the episode and 0 otherwise
        obs_tp1 gets ignored, but must be of the valid shape.
        dtype must be float32 and shape must be (batch_size,)
    weight: np.array
        imporance weights for every element of the batch (gradient is multiplied
        by the importance weight) dtype must be float32 and shape must be (batch_size,)

    Returns
    -------
    td_error: np.array
        a list of differences between Q(s,a) and the target in Bellman's equation.
        dtype is float32 and shape is (batch_size,)

======= update_target ========

    copy the parameters from optimized P function to the target P function.
    In distributional RL we actually optimize the following error:

        ThTz(P') * log(P)

    Where P' is lagging behind P to stablize the learning.

"""
import tensorflow as tf
import numpy as np
import baselines.common.tf_util as U
# from .static import build_z


def default_param_noise_filter(var):
    if var not in tf.trainable_variables():
        # We never perturb non-trainable vars.
        return False
    if "fully_connected" in var.name:
        # We perturb fully-connected layers.
        return True

    # The remaining layers are likely conv or layer norm layers, which we do not wish to
    # perturb (in the former case because they only extract features, in the latter case because
    # we use them for normalization purposes). If you change your network, you will likely want
    # to re-consider which layers to perturb and which to keep untouched.
    return False


def p_to_q(pi, mu, dist_params):
    # z, _ = build_z(**dist_params)
    # print(z, p_values)
    # return tf.tensordot(p_values, z, [[-1], [-1]])

    return tf.reduce_sum(tf.multiply(pi, mu), axis=-1)


def pick_action(pi, mu, dist_params):
    q_values = p_to_q(pi, mu, dist_params)
    # greedy case
    actions = tf.argmax(q_values, axis=1)

    # sparsemax case
    # q_values = q_values / 1.
    # policy = tf.contrib.sparsemax.sparsemax(q_values)
    # actions = tf.multinomial(tf.log(policy),1)

    # actions = tf.reshape(actions,[-1])

    return actions


def build_act(make_obs_ph, p_dist_func, num_actions, dist_params, scope="distdeepq", reuse=None):
    """Creates the act function:

    Parameters
    ----------
    make_obs_ph: str -> tf.placeholder or TfInput
        a function that take a name and creates a placeholder of input with that name
    p_dist_func: (tf.Variable, int, str, bool) -> tf.Variable
        the model that takes the following inputs:
            observation_in: object
                the output of observation placeholder
            num_actions: int
                number of actions
            scope: str
            reuse: bool
                should be passed to outer variable scope
        and returns a tensor of shape (batch_size, num_actions) with values of every action.
    num_actions: int
        number of actions.
    scope: str or VariableScope
        optional scope for variable_scope.
    reuse: bool or None
        whether or not the variables should be reused. To be able to reuse the scope must be given.

    Returns
    -------
    act: (tf.Variable, bool, float) -> tf.Variable
        function to select and action given observation.
`       See the top of the file for details.
    """
    with tf.variable_scope(scope, reuse=reuse):
        observations_ph = U.ensure_tf_input(make_obs_ph("observation"))
        stochastic_ph = tf.placeholder(tf.bool, (), name="stochastic")
        update_eps_ph = tf.placeholder(tf.float32, (), name="update_eps")

        eps = tf.get_variable("eps", (), initializer=tf.constant_initializer(0))

        pi, sigma, mu = p_dist_func(observations_ph.get(), num_actions, dist_params['nb_atoms'], scope="q_func")
        deterministic_actions = pick_action(pi, mu, dist_params)

        batch_size = tf.shape(observations_ph.get())[0]
        random_actions = tf.random_uniform(tf.stack([batch_size]), minval=0, maxval=num_actions, dtype=tf.int64)
        chose_random = tf.random_uniform(tf.stack([batch_size]), minval=0, maxval=1, dtype=tf.float32) < eps
        stochastic_actions = tf.where(chose_random, random_actions, deterministic_actions)

        output_actions = tf.cond(stochastic_ph, lambda: stochastic_actions, lambda: deterministic_actions)
        update_eps_expr = eps.assign(tf.cond(update_eps_ph >= 0, lambda: update_eps_ph, lambda: eps))
        act = U.function(inputs=[observations_ph, stochastic_ph, update_eps_ph],
                         outputs=output_actions,
                         givens={update_eps_ph: -1.0, stochastic_ph: True},
                         updates=[update_eps_expr])
        return act


def build_train(make_obs_ph, p_dist_func, num_actions, optimizer, grad_norm_clipping=None, gamma=1.0,
                double_q=True, scope="distdeepq", reuse=None, param_noise=False, param_noise_filter_func=None,
                dist_params=None):
    """Creates the train function:

    Parameters
    ----------
    make_obs_ph: str -> tf.placeholder or TfInput
        a function that takes a name and creates a placeholder of input with that name
    p_dist_func: (tf.Variable, int, str, bool) -> tf.Variable
        the model that takes the following inputs:
            observation_in: object
                the output of observation placeholder
            num_actions: int
                number of actions
            scope: str
            reuse: bool
                should be passed to outer variable scope
        and returns a tensor of shape (batch_size, num_actions) with values of every action.
    num_actions: int
        number of actions
    reuse: bool
        whether or not to reuse the graph variables
    optimizer: tf.train.Optimizer
        optimizer to use for the Q-learning objective.
    grad_norm_clipping: float or None
        clip gradient norms to this value. If None no clipping is performed.
    gamma: float
        discount rate.
    double_q: bool
        if true will use Double Q Learning (https://arxiv.org/abs/1509.06461).
        In general it is a good idea to keep it enabled.
    scope: str or VariableScope
        optional scope for variable_scope.
    reuse: bool or None
        whether or not the variables should be reused. To be able to reuse the scope must be given.
    param_noise: bool
        whether or not to use parameter space noise (https://arxiv.org/abs/1706.01905)
    param_noise_filter_func: tf.Variable -> bool
        function that decides whether or not a variable should be perturbed. Only applicable
        if param_noise is True. If set to None, default_param_noise_filter is used by default.

    Returns
    -------
    act: (tf.Variable, bool, float) -> tf.Variable
        function to select and action given observation.
`       See the top of the file for details.
    train: (object, np.array, np.array, object, np.array, np.array) -> np.array
        optimize the error in Bellman's equation.
`       See the top of the file for details.
    update_target: () -> ()
        copy the parameters from optimized Q function to the target Q function.
`       See the top of the file for details.
    debug: {str: function}
        a bunch of functions to print debug data like q_values.
    """

    if param_noise:
        raise ValueError('parameter noise not supported')
    else:
        act_f = build_act(make_obs_ph, p_dist_func, num_actions, dist_params, scope=scope, reuse=reuse)

    with tf.variable_scope(scope, reuse=reuse):
        # set up placeholders
        obs_t_input = U.ensure_tf_input(make_obs_ph("obs_t"))
        act_t_ph = tf.placeholder(tf.int32, [None], name="action")
        rew_t_ph = tf.placeholder(tf.float32, [None], name="reward")
        obs_tp1_input = U.ensure_tf_input(make_obs_ph("obs_tp1"))
        done_mask_ph = tf.placeholder(tf.float32, [None], name="done")
        importance_weights_ph = tf.placeholder(tf.float32, [None], name="weight")

        # =====================================================================================
        # q network evaluation
        # p_t = p_dist_func(obs_t_input.get(), num_actions, dist_params['nb_atoms'], scope="q_func", reuse=True)  # reuse parameters from act
        pi_t_, sigma_t_, mu_t_ = p_dist_func(obs_t_input.get(), num_actions, dist_params['nb_atoms'], scope="q_func", reuse=True)
        q_t = p_to_q(pi_t_, mu_t_, dist_params)  # reuse parameters from act
        q_func_vars = U.scope_vars(U.absolute_scope_name("q_func"))

        # target q network evalution
        pi_tp1_, sigma_tp1_, mu_tp1_ = p_dist_func(obs_tp1_input.get(), num_actions, dist_params['nb_atoms'], scope="target_q_func")
        q_tp1 = p_to_q(pi_tp1_, mu_tp1_, dist_params)
        target_q_func_vars = U.scope_vars(U.absolute_scope_name("target_q_func"))

        # TODO: use double

        a_next = tf.argmax(q_tp1, 1, output_type=tf.int32)
        batch_dim = tf.shape(rew_t_ph)[0]
        # TODO: r+gamma*Z, build_mog (target distribution)
        pi_tg, sigma_tg, mu_tg, debug = build_mog(pi_tp1_, sigma_tp1_, mu_tp1_, rew_t_ph, a_next, gamma,
                                                  batch_dim, done_mask_ph, dist_params)
        # ThTz, debug = build_categorical_alg(p_tp1, rew_t_ph, a_next, gamma, batch_dim, done_mask_ph, dist_params)

        # compute the error (potentially clipped)
        # TODO: only for the chosen action (update distribution)
        cat_idx = tf.transpose(tf.reshape(tf.concat([tf.range(batch_dim), act_t_ph], axis=0), [2, batch_dim]))
        pi = tf.gather_nd(pi_t_, cat_idx)
        sigma = tf.gather_nd(sigma_t_, cat_idx)
        mu = tf.gather_nd(mu_t_, cat_idx)

        # loss
        # cross_entropy = -1 * ThTz * tf.log(p_t_next)

        # second moment of JTD
        # z, _ = build_z(**dist_params)
        # zz = tf.reshape(tf.tile(z, [batch_dim]), [batch_dim, dist_params['nb_atoms']])
        # loss = zz * zz * (ThTz - p_t_next) * (ThTz - p_t_next)
        # errors = tf.sqrt(tf.reduce_sum(loss, axis=-1))

        # second moment JTD of MoG
        errors = (calc_integral(pi, sigma, mu, pi, sigma, mu, batch_dim, dist_params) +
                calc_integral(pi_tg, sigma_tg, mu_tg, pi_tg, sigma_tg, mu_tg, batch_dim, dist_params) -
                2*calc_integral(pi, sigma, mu, pi_tg, sigma_tg, mu_tg, batch_dim, dist_params))
        mean_error = tf.reduce_mean(errors)  # average over batches

        # compute optimization op (potentially with gradient clipping)
        if grad_norm_clipping is not None:
            optimize_expr = U.minimize_and_clip(optimizer,
                                                mean_error,
                                                var_list=q_func_vars,
                                                clip_val=grad_norm_clipping)
        else:
            optimize_expr = optimizer.minimize(mean_error, var_list=q_func_vars)

        # =====================================================================================

        # update_target_fn will be called periodically to copy Q network to target Q network
        update_target_expr = []
        for var, var_target in zip(sorted(q_func_vars, key=lambda v: v.name),
                                   sorted(target_q_func_vars, key=lambda v: v.name)):
            update_target_expr.append(var_target.assign(var))
        update_target_expr = tf.group(*update_target_expr)

        # Create callable functions
        train = U.function(
            inputs=[
                obs_t_input,
                act_t_ph,
                rew_t_ph,
                obs_tp1_input,
                done_mask_ph,
                importance_weights_ph
            ],
            outputs=mean_error,
            updates=[optimize_expr]
        )
        update_target = U.function([], [], updates=[update_target_expr])

        q_values = U.function([obs_t_input], q_t)

        return act_f, train, update_target, {'q_values': q_values,
                                             'mu': mu_tg,
                                             'sigma': sigma_tg,
                                             'pi': pi_tg,
                                             'second_moment_jtd': errors,
                                             }


def build_mog(pi_tp1, sigma_tp1, mu_tp1, rew_t_ph, a_next, gamma, batch_dim, done_mask_ph, dist_params):

    nb_mix = dist_params['nb_atoms']
    with tf.variable_scope('mixture'):
        cat_idx = tf.transpose(tf.reshape(tf.concat([tf.range(batch_dim), a_next], axis=0), [2, batch_dim]))
        pi_best = tf.gather_nd(pi_tp1, cat_idx)
        sigma_best = tf.gather_nd(sigma_tp1, cat_idx)
        mu_best = tf.gather_nd(mu_tp1, cat_idx)

        big_r = tf.transpose(tf.reshape(tf.tile(rew_t_ph, [nb_mix]), [nb_mix, batch_dim]))

        sigma_best = gamma * gamma * sigma_best
        mu_best = tf.add(big_r, gamma * tf.einsum('ij,i->ij', mu_best, 1.-done_mask_ph))
        # sess = tf.InteractiveSession()
        # print(sess.run(pi_best))
        # print(sess.run(mu_best))
        # print(sess.run(sigma_best))

    return pi_best, sigma_best, mu_best, {'mu_best': mu_best}


def calc_integral(pi_i, sigma_i, mu_i, pi_j, sigma_j, mu_j, batch_dim, dist_params):

    nb_mix = dist_params['nb_atoms']
    summ = tf.zeros([batch_dim])

    for i in range(nb_mix):
        for j in range(nb_mix):
            # JTD
            sum_sigma = tf.add(tf.gather(sigma_i, i, axis=-1), tf.gather(sigma_j, j, axis=-1))
            exp = tf.exp(-tf.square(tf.gather(mu_i, i, axis=-1)-tf.gather(mu_j, j, axis=-1))/(2*sum_sigma))
            summ = tf.add(summ, exp*tf.gather(pi_i, i, axis=-1)*tf.gather(pi_j, j, axis=-1)/tf.sqrt(sum_sigma))

            # MMD
            #sum_sigma = tf.add(tf.gather(sigma_i, i, axis=-1), tf.gather(sigma_j, j, axis=-1))
            #inte = tf.add(sum_sigma, tf.square(tf.gather(mu_i, i, axis=-1)-tf.gather(mu_j, j, axis=-1)))
            #summ = tf.add(summ, -inte*tf.gather(pi_i, i, axis=-1)*tf.gather(pi_j, j, axis=-1))

    return summ*tf.cast(tf.constant(1/np.sqrt(2*np.pi)), 'float32')

    #         sum_sigma = tf.add(tf.gather(sigma_i, i, axis=-1), tf.gather(sigma_j, j, axis=-1))
    #         mul_sigma = tf.multiply(tf.gather(sigma_i, i, axis=-1), tf.gather(sigma_j, j, axis=-1))
    #         w_sum = (tf.gather(mu_i, i, axis=-1)*tf.gather(sigma_j, j, axis=-1) +
    #             tf.gather(mu_j, j, axis=-1)*tf.gather(sigma_i, i, axis=-1)) / sum_sigma
    #         exp = tf.exp(-tf.square(tf.gather(mu_i, i, axis=-1)-tf.gather(mu_j, j, axis=-1))/(2*sum_sigma))
    #         summation = tf.add(summation, (mul_sigma/sum_sigma + tf.square(w_sum)) * exp *
    #                       tf.gather(pi_i, i, axis=-1)*tf.gather(pi_j, j, axis=-1) / tf.sqrt(sum_sigma))
    #
    # return summation*tf.cast(tf.constant(1/np.sqrt(32*np.pi)), 'float32')

#
# def build_categorical_alg(p_ph, r_ph, a_next, gamma, batch_dim, done_mask, dist_params):
#     """
#     Builds the vectorized cathegorical algorithm following equation (7) of
#     'A Distributional Perspective on Reinforcement Learning' - https://arxiv.org/abs/1707.06887
#     """
#     z, dz = build_z(**dist_params)
#     Vmin, Vmax, nb_atoms = dist_params['Vmin'], dist_params['Vmax'], dist_params['nb_atoms']
#     with tf.variable_scope('categorical'):
#
#         cat_idx = tf.transpose(tf.reshape(tf.concat([tf.range(batch_dim), a_next], axis=0), [2, batch_dim]))
#         p_best = tf.gather_nd(p_ph, cat_idx)
#
#         big_z = tf.reshape(tf.tile(z, [batch_dim]), [batch_dim, nb_atoms])
#         big_r = tf.transpose(tf.reshape(tf.tile(r_ph, [nb_atoms]), [nb_atoms, batch_dim]))
#
#         Tz = tf.clip_by_value(big_r + gamma * tf.einsum('ij,i->ij', big_z, 1.-done_mask), Vmin, Vmax)
#
#         big_Tz = tf.reshape(tf.tile(Tz, [1, nb_atoms]), [-1, nb_atoms, nb_atoms])
#         big_big_z = tf.reshape(tf.tile(big_z, [1, nb_atoms]), [-1, nb_atoms, nb_atoms])
#
#         Tzz = tf.abs(big_Tz - tf.transpose(big_big_z, [0, 2, 1])) / dz
#         Thz = tf.clip_by_value(1 - Tzz, 0, 1)
#
#         ThTz = tf.einsum('ijk,ik->ij', Thz, p_best)
#
#     return ThTz, {'p_best': p_best}


