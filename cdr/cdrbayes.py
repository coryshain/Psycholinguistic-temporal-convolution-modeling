import pandas as pd
pd.options.mode.chained_assignment = None

import tensorflow as tf
from tensorflow.contrib.distributions import MultivariateNormalTriL, Normal, SinhArcsinh

tf_config = tf.ConfigProto()
tf_config.gpu_options.allow_growth = True

from .util import *
from .cdrbase import CDR
from .kwargs import CDRBAYES_INITIALIZATION_KWARGS




######################################################
#
#  BAYESIAN IMPLEMENTATION OF CDR
#
######################################################

class CDRBayes(CDR):

    _INITIALIZATION_KWARGS = CDRBAYES_INITIALIZATION_KWARGS

    _doc_header = """
        A CDR implementation fitted using black box variational Bayes.
    """
    _doc_args = CDR._doc_args
    _doc_kwargs = CDR._doc_kwargs
    _doc_kwargs += '\n' + '\n'.join([' ' * 8 + ':param %s' % x.key + ': ' + '; '.join([x.dtypes_str(), x.descr]) + ' **Default**: ``%s``.' % (x.default_value if not isinstance(x.default_value, str) else "'%s'" % x.default_value) for x in _INITIALIZATION_KWARGS])
    __doc__ = _doc_header + _doc_args + _doc_kwargs


    #####################################################
    #
    #  Native methods
    #
    #####################################################

    def __init__(self, form_str, X, y, **kwargs):
        super(CDRBayes, self).__init__(
            form_str,
            X,
            y,
            **kwargs
        )

        for kwarg in CDRBayes._INITIALIZATION_KWARGS:
            setattr(self, kwarg.key, kwargs.pop(kwarg.key, kwarg.default_value))

        self._initialize_metadata()

        self.build()

    def _initialize_metadata(self):
        super(CDRBayes, self)._initialize_metadata()

        self.is_bayesian = True

        self.parameter_table_columns = ['Mean', '2.5%', '97.5%']

        if self.intercept_init is None:
            if self.standardize_response:
                self.intercept_init = 0.
            else:
                self.intercept_init = self.y_train_mean
        if self.intercept_prior_sd is None:
            if self.standardize_response:
                self.intercept_prior_sd = self.prior_sd_scaling_coefficient
            else:
                self.intercept_prior_sd = self.y_train_sd * self.prior_sd_scaling_coefficient
        if self.coef_prior_sd is None:
            if self.standardize_response:
                self.coef_prior_sd = self.prior_sd_scaling_coefficient
            else:
                self.coef_prior_sd = self.y_train_sd * self.prior_sd_scaling_coefficient
        if self.y_sd_prior_sd is None:
            if self.standardize_response:
                self.y_sd_prior_sd = self.y_sd_prior_sd_scaling_coefficient
            else:
                self.y_sd_prior_sd = self.y_train_sd * self.y_sd_prior_sd_scaling_coefficient

        self.kl_penalties = {}

        with self.sess.as_default():
            with self.sess.graph.as_default():
                # Alias prior widths for use in multivariate mode
                self.intercept_joint_sd = self.intercept_prior_sd
                self.coef_joint_sd = self.coef_prior_sd
                self.irf_param_joint_sd = self.irf_param_prior_sd
                self.ranef_to_fixef_joint_sd_ratio = self.ranef_to_fixef_prior_sd_ratio

                # Define initialization constants
                self.intercept_prior_sd_tf = tf.constant(float(self.intercept_prior_sd), dtype=self.FLOAT_TF)
                self.intercept_posterior_sd_init = self.intercept_prior_sd_tf * self.posterior_to_prior_sd_ratio
                self.intercept_ranef_prior_sd_tf = self.intercept_prior_sd_tf * self.ranef_to_fixef_prior_sd_ratio
                self.intercept_ranef_posterior_sd_init = self.intercept_posterior_sd_init * self.ranef_to_fixef_prior_sd_ratio

                self.coef_prior_sd_tf = tf.constant(float(self.coef_prior_sd), dtype=self.FLOAT_TF)
                self.coef_posterior_sd_init = self.coef_prior_sd_tf * self.posterior_to_prior_sd_ratio
                self.coef_ranef_prior_sd_tf = self.coef_prior_sd_tf * self.ranef_to_fixef_prior_sd_ratio
                self.coef_ranef_posterior_sd_init = self.coef_posterior_sd_init * self.ranef_to_fixef_prior_sd_ratio

                self.irf_param_prior_sd_tf = tf.constant(float(self.irf_param_prior_sd), dtype=self.FLOAT_TF)
                self.irf_param_posterior_sd_init = self.irf_param_prior_sd_tf * self.posterior_to_prior_sd_ratio
                self.irf_param_ranef_prior_sd_tf = self.irf_param_prior_sd_tf * self.ranef_to_fixef_prior_sd_ratio
                self.irf_param_ranef_posterior_sd_init = self.irf_param_posterior_sd_init * self.ranef_to_fixef_prior_sd_ratio

                self.y_sd_prior_sd_tf = tf.constant(float(self.y_sd_prior_sd), dtype=self.FLOAT_TF)
                self.y_sd_posterior_sd_init = self.y_sd_prior_sd_tf * self.posterior_to_prior_sd_ratio

                self.y_skewness_prior_sd_tf = tf.constant(float(self.y_skewness_prior_sd), dtype=self.FLOAT_TF)
                self.y_skewness_posterior_sd_init = self.y_skewness_prior_sd_tf * self.posterior_to_prior_sd_ratio

                self.y_tailweight_prior_loc_tf = tf.constant(1., dtype=self.FLOAT_TF)
                self.y_tailweight_posterior_loc_init = tf.constant(1., dtype=self.FLOAT_TF)
                self.y_tailweight_prior_sd_tf = tf.constant(float(self.y_tailweight_prior_sd), dtype=self.FLOAT_TF)
                self.y_tailweight_posterior_sd_init = self.y_tailweight_prior_sd_tf * self.posterior_to_prior_sd_ratio

                if self.constraint.lower() == 'softplus':
                    self.intercept_prior_sd_unconstrained = tf.contrib.distributions.softplus_inverse(self.intercept_prior_sd_tf)
                    self.intercept_posterior_sd_init_unconstrained = tf.contrib.distributions.softplus_inverse(self.intercept_posterior_sd_init)
                    self.intercept_ranef_prior_sd_unconstrained = tf.contrib.distributions.softplus_inverse(self.intercept_ranef_prior_sd_tf)
                    self.intercept_ranef_posterior_sd_init_unconstrained = tf.contrib.distributions.softplus_inverse(self.intercept_ranef_posterior_sd_init)

                    self.coef_prior_sd_unconstrained = tf.contrib.distributions.softplus_inverse(self.coef_prior_sd_tf)
                    self.coef_posterior_sd_init_unconstrained = tf.contrib.distributions.softplus_inverse(self.coef_posterior_sd_init)
                    self.coef_ranef_prior_sd_unconstrained = tf.contrib.distributions.softplus_inverse(self.coef_ranef_prior_sd_tf)
                    self.coef_ranef_posterior_sd_init_unconstrained = tf.contrib.distributions.softplus_inverse(self.coef_ranef_posterior_sd_init)

                    self.irf_param_prior_sd_unconstrained = tf.contrib.distributions.softplus_inverse(self.irf_param_prior_sd_tf)
                    self.irf_param_posterior_sd_init_unconstrained = tf.contrib.distributions.softplus_inverse(self.irf_param_posterior_sd_init)
                    self.irf_param_ranef_prior_sd_unconstrained = tf.contrib.distributions.softplus_inverse(self.irf_param_ranef_prior_sd_tf)
                    self.irf_param_ranef_posterior_sd_init_unconstrained = tf.contrib.distributions.softplus_inverse(self.irf_param_ranef_posterior_sd_init)

                    self.y_sd_prior_sd_unconstrained = tf.contrib.distributions.softplus_inverse(self.y_sd_prior_sd_tf)
                    self.y_sd_posterior_sd_init_unconstrained = tf.contrib.distributions.softplus_inverse(self.y_sd_posterior_sd_init)

                    self.y_skewness_prior_sd_unconstrained = tf.contrib.distributions.softplus_inverse(self.y_skewness_prior_sd_tf)
                    self.y_skewness_posterior_sd_init_unconstrained = tf.contrib.distributions.softplus_inverse(self.y_skewness_posterior_sd_init)

                    self.y_tailweight_prior_loc_unconstrained = tf.contrib.distributions.softplus_inverse(self.y_tailweight_prior_loc_tf)
                    self.y_tailweight_posterior_loc_init_unconstrained = tf.contrib.distributions.softplus_inverse(self.y_tailweight_posterior_loc_init)
                    self.y_tailweight_prior_sd_unconstrained = tf.contrib.distributions.softplus_inverse(self.y_tailweight_prior_sd_tf)
                    self.y_tailweight_posterior_sd_init_unconstrained = tf.contrib.distributions.softplus_inverse(self.y_tailweight_posterior_sd_init)

                elif self.constraint.lower() == 'abs':
                    self.intercept_prior_sd_unconstrained = self.intercept_prior_sd_tf
                    self.intercept_posterior_sd_init_unconstrained = self.intercept_posterior_sd_init
                    self.intercept_ranef_prior_sd_unconstrained = self.intercept_ranef_prior_sd_tf
                    self.intercept_ranef_posterior_sd_init_unconstrained = self.intercept_ranef_posterior_sd_init

                    self.coef_prior_sd_unconstrained = self.coef_prior_sd_tf
                    self.coef_posterior_sd_init_unconstrained = self.coef_posterior_sd_init
                    self.coef_ranef_prior_sd_unconstrained = self.coef_ranef_prior_sd_tf
                    self.coef_ranef_posterior_sd_init_unconstrained = self.coef_ranef_posterior_sd_init

                    self.irf_param_prior_sd_unconstrained = self.irf_param_prior_sd_tf
                    self.irf_param_posterior_sd_init_unconstrained = self.irf_param_posterior_sd_init
                    self.irf_param_ranef_prior_sd_unconstrained = self.irf_param_ranef_prior_sd_tf
                    self.irf_param_ranef_posterior_sd_init_unconstrained = self.irf_param_ranef_posterior_sd_init

                    self.y_sd_prior_sd_unconstrained = self.y_sd_prior_sd_tf
                    self.y_sd_posterior_sd_init_unconstrained = self.y_sd_posterior_sd_init

                    self.y_skewness_prior_sd_unconstrained = self.y_skewness_prior_sd_tf
                    self.y_skewness_posterior_sd_init_unconstrained = self.y_skewness_posterior_sd_init

                    self.y_tailweight_prior_loc_unconstrained = self.y_tailweight_prior_loc_tf
                    self.y_tailweight_posterior_loc_init_unconstrained = self.y_tailweight_posterior_loc_init
                    self.y_tailweight_prior_sd_unconstrained = self.y_tailweight_prior_sd_tf
                    self.y_tailweight_posterior_sd_init_unconstrained = self.y_tailweight_posterior_sd_init

                else:
                    raise ValueError('Unrecognized constraint function "%s"' % self.constraint)

    def _pack_metadata(self):
        md = super(CDRBayes, self)._pack_metadata()
        for kwarg in CDRBayes._INITIALIZATION_KWARGS:
            md[kwarg.key] = getattr(self, kwarg.key)

        return md

    def _unpack_metadata(self, md):
        super(CDRBayes, self)._unpack_metadata(md)

        for kwarg in CDRBayes._INITIALIZATION_KWARGS:
            setattr(self, kwarg.key, md.pop(kwarg.key, kwarg.default_value))

        if len(md) > 0:
            stderr('Saved model contained unrecognized attributes %s which are being ignored\n' %sorted(list(md.keys())))


    ######################################################
    #
    #  Network Initialization
    #
    ######################################################

    def _initialize_inputs(self, n_impulse):
        super(CDRBayes, self)._initialize_inputs(n_impulse)
        with self.sess.as_default():
            with self.sess.graph.as_default():
                self.use_MAP_mode = tf.placeholder_with_default(tf.logical_not(self.training), shape=[], name='use_MAP_mode')

    def initialize_intercept(self, ran_gf=None):
        with self.sess.as_default():
            with self.sess.graph.as_default():
                if ran_gf is None:
                    # Posterior distribution
                    intercept_q_loc = tf.Variable(
                        self.intercept_init_tf,
                        name='intercept_q_loc'
                    )

                    intercept_q_scale = tf.Variable(
                        self.intercept_posterior_sd_init_unconstrained,
                        name='intercept_q_scale'
                    )

                    intercept_q_dist = Normal(
                        loc=intercept_q_loc,
                        scale=self.constraint_fn(intercept_q_scale) + self.epsilon,
                        name='intercept_q'
                    )

                    intercept = tf.cond(self.use_MAP_mode, intercept_q_dist.mean, intercept_q_dist.sample)

                    intercept_summary = intercept_q_dist.mean()

                    if self.declare_priors_fixef:
                        # Prior distribution
                        intercept_prior_dist = Normal(
                            loc=self.intercept_init_tf,
                            scale=self.intercept_prior_sd_tf,
                            name='intercept'
                        )
                        self.kl_penalties['intercept'] = {
                            'loc': self.intercept_init,
                            'scale': self.intercept_prior_sd,
                            'val': intercept_q_dist.kl_divergence(intercept_prior_dist)
                        }

                else:
                    rangf_n_levels = self.rangf_n_levels[self.rangf.index(ran_gf)] - 1

                    # Posterior distribution
                    intercept_q_loc = tf.Variable(
                        tf.zeros([rangf_n_levels], dtype=self.FLOAT_TF),
                        name='intercept_q_loc_by_%s' % sn(ran_gf)
                    )

                    intercept_q_scale = tf.Variable(
                        tf.ones([rangf_n_levels], dtype=self.FLOAT_TF) * self.intercept_ranef_posterior_sd_init_unconstrained,
                        name='intercept_q_scale_by_%s' % sn(ran_gf)
                    )

                    intercept_q_dist = Normal(
                        loc=intercept_q_loc,
                        scale=self.constraint_fn(intercept_q_scale) + self.epsilon,
                        name='intercept_q_by_%s' % sn(ran_gf)
                    )

                    intercept = tf.cond(self.use_MAP_mode, intercept_q_dist.mean, intercept_q_dist.sample)

                    intercept_summary = intercept_q_dist.mean()

                    if self.declare_priors_ranef:
                        # Prior distribution
                        intercept_prior_dist = Normal(
                            loc=0.,
                            scale=self.intercept_ranef_prior_sd_tf,
                            name='intercept_by_%s' % sn(ran_gf)
                        )
                        self.kl_penalties['intercept_by_%s' % sn(ran_gf)] = {
                            'loc': 0.,
                            'scale': self.intercept_prior_sd * self.ranef_to_fixef_prior_sd_ratio,
                            'val': intercept_q_dist.kl_divergence(intercept_prior_dist)
                        }

                return intercept, intercept_summary

    def initialize_coefficient(self, coef_ids=None, ran_gf=None, suffix=None):
        if coef_ids is None:
            coef_ids = self.coef_names
        if suffix is None:
            suffix = ''
        elif suffix.startswith('_'):
            suffix = '_' + suffix
        with self.sess.as_default():
            with self.sess.graph.as_default():
                if ran_gf is None:
                    # Posterior distribution
                    coefficient_q_loc = tf.Variable(
                        tf.zeros([len(coef_ids)], dtype=self.FLOAT_TF),
                        name='coefficient%s_q_loc' % suffix
                    )

                    coefficient_q_scale = tf.Variable(
                        tf.ones([len(coef_ids)], dtype=self.FLOAT_TF) * self.coef_posterior_sd_init_unconstrained,
                        name='coefficient%s_q_scale' % suffix
                    )

                    coefficient_q_dist = Normal(
                        loc=coefficient_q_loc,
                        scale=self.constraint_fn(coefficient_q_scale) + self.epsilon,
                        name='coefficient%s_q' % suffix
                    )

                    coefficient = tf.cond(self.use_MAP_mode, coefficient_q_dist.mean, coefficient_q_dist.sample)

                    coefficient_summary = coefficient_q_dist.mean()

                    if self.declare_priors_fixef:
                        # Prior distribution
                        coefficient_prior_dist = Normal(
                            loc=0.,
                            scale=self.coef_prior_sd_tf,
                            name='coefficient%s' % suffix
                        )
                        self.kl_penalties['coefficient%s' % suffix] = {
                            'loc': 0.,
                            'scale': self.coef_prior_sd,
                            'val': coefficient_q_dist.kl_divergence(coefficient_prior_dist)
                        }

                else:
                    rangf_n_levels = self.rangf_n_levels[self.rangf.index(ran_gf)] - 1

                    # Posterior distribution
                    coefficient_q_loc = tf.Variable(
                        tf.zeros([rangf_n_levels, len(coef_ids)], dtype=self.FLOAT_TF),
                        name='coefficient%s_q_loc_by_%s' % (suffix, sn(ran_gf))
                    )

                    coefficient_q_scale = tf.Variable(
                        tf.ones([rangf_n_levels, len(coef_ids)], dtype=self.FLOAT_TF) * self.coef_ranef_posterior_sd_init_unconstrained,
                        name='coefficient%s_q_scale_by_%s' % (suffix, sn(ran_gf))
                    )

                    coefficient_q_dist = Normal(
                        loc=coefficient_q_loc,
                        scale=self.constraint_fn(coefficient_q_scale) + self.epsilon,
                        name='coefficient%s_q_by_%s' % (suffix, sn(ran_gf))
                    )

                    coefficient = tf.cond(self.use_MAP_mode, coefficient_q_dist.mean, coefficient_q_dist.sample)

                    coefficient_summary = coefficient_q_dist.mean()

                    if self.declare_priors_ranef:
                        # Prior distribution
                        coefficient_prior_dist = Normal(
                            loc=0.,
                            scale=self.coef_ranef_prior_sd_tf,
                            name='coefficient%s_by_%s' % (suffix, sn(ran_gf))
                        )
                        self.kl_penalties['coefficient%s_by_%s' % (suffix, sn(ran_gf))] = {
                            'loc': 0.,
                            'scale': self.coef_prior_sd * self.ranef_to_fixef_prior_sd_ratio,
                            'val': coefficient_q_dist.kl_divergence(coefficient_prior_dist)
                        }

                return coefficient, coefficient_summary

    def initialize_interaction(self, interaction_ids=None, ran_gf=None):
        if interaction_ids is None:
            interaction_ids = self.interaction_names
        with self.sess.as_default():
            with self.sess.graph.as_default():
                if ran_gf is None:
                    # Posterior distribution
                    interaction_q_loc = tf.Variable(
                        tf.zeros([len(interaction_ids)], dtype=self.FLOAT_TF),
                        name='interaction_q_loc'
                    )

                    interaction_q_scale = tf.Variable(
                        tf.ones([len(interaction_ids)], dtype=self.FLOAT_TF) * self.coef_posterior_sd_init_unconstrained,
                        name='interaction_q_scale'
                    )

                    interaction_q_dist = Normal(
                        loc=interaction_q_loc,
                        scale=self.constraint_fn(interaction_q_scale) + self.epsilon,
                        name='interaction_q'
                    )

                    interaction = tf.cond(self.use_MAP_mode, interaction_q_dist.mean, interaction_q_dist.sample)

                    interaction_summary = interaction_q_dist.mean()

                    if self.declare_priors_fixef:
                        # Prior distribution
                        interaction_prior_dist = Normal(
                            loc=0.,
                            scale=self.coef_prior_sd_tf,
                            name='interaction'
                        )
                        self.kl_penalties['interaction'] = {
                            'loc': 0.,
                            'scale': self.coef_prior_sd,
                            'val': interaction_q_dist.kl_divergence(interaction_prior_dist)
                        }
                else:
                    rangf_n_levels = self.rangf_n_levels[self.rangf.index(ran_gf)] - 1
                    # Posterior distribution
                    interaction_q_loc = tf.Variable(
                        tf.zeros([rangf_n_levels, len(interaction_ids)], dtype=self.FLOAT_TF),
                        name='interaction_q_loc_by_%s' % sn(ran_gf)
                    )

                    interaction_q_scale = tf.Variable(
                        tf.ones([rangf_n_levels, len(interaction_ids)], dtype=self.FLOAT_TF) * self.coef_ranef_posterior_sd_init_unconstrained,
                        name='interaction_q_scale_by_%s' % sn(ran_gf)
                    )

                    interaction_q_dist = Normal(
                        loc=interaction_q_loc,
                        scale=self.constraint_fn(interaction_q_scale) + self.epsilon,
                        name='interaction_q_by_%s' % sn(ran_gf)
                    )

                    interaction = tf.cond(self.use_MAP_mode, interaction_q_dist.mean, interaction_q_dist.sample)

                    interaction_summary = interaction_q_dist.mean()

                    if self.declare_priors_ranef:
                        # Prior distribution
                        interaction_prior_dist = Normal(
                            loc=0.,
                            scale=self.coef_ranef_prior_sd_tf,
                            name='interaction_by_%s' % sn(ran_gf)
                        )
                        self.kl_penalties['interaction_by_%s' % sn(ran_gf)] = {
                            'loc': 0.,
                            'scale': self.coef_prior_sd * self.ranef_to_fixef_prior_sd_ratio,
                            'val': interaction_q_dist.kl_divergence(interaction_prior_dist)
                        }

                return interaction, interaction_summary

    def initialize_irf_param_unconstrained(self, param_name, ids, mean=0., ran_gf=None):
        with self.sess.as_default():
            with self.sess.graph.as_default():
                if ran_gf is None:
                    # Posterior distribution
                    param_q_loc = tf.Variable(
                        tf.ones([1, len(ids)], dtype=self.FLOAT_TF) * mean,
                        name=sn('%s_q_loc_%s' % (param_name, '-'.join(ids)))
                    )

                    param_q_scale = tf.Variable(
                        tf.ones([1, len(ids)], dtype=self.FLOAT_TF) * self.irf_param_posterior_sd_init_unconstrained,
                        name=sn('%s_q_scale_%s' % (param_name, '-'.join(ids)))
                    )

                    param_q_dist = Normal(
                        loc=param_q_loc,
                        scale=self.constraint_fn(param_q_scale) + self.epsilon,
                        name=sn('%s_q_%s' % (param_name, '-'.join(ids)))
                    )

                    param = tf.cond(self.use_MAP_mode, param_q_dist.mean, param_q_dist.sample)

                    param_summary = param_q_dist.mean()

                    if self.declare_priors_fixef:
                        # Prior distribution
                        param_prior_dist = Normal(
                            loc=mean,
                            scale=self.irf_param_prior_sd,
                            name=sn('%s_%s' % (param_name, '-'.join(ids)))
                        )
                        self.kl_penalties['%s_%s' % (param_name, '-'.join(ids))] = {
                            'loc': mean,
                            'scale': self.irf_param_prior_sd,
                            'val': param_q_dist.kl_divergence(param_prior_dist)
                        }

                else:
                    rangf_n_levels = self.rangf_n_levels[self.rangf.index(ran_gf)] - 1

                    # Posterior distribution
                    param_q_loc = tf.Variable(
                        tf.zeros([rangf_n_levels, len(ids)], dtype=self.FLOAT_TF),
                        name=sn('%s_q_loc_%s_by_%s' % (param_name, '-'.join(ids), sn(ran_gf)))
                    )

                    param_q_scale = tf.Variable(
                        tf.ones([rangf_n_levels, len(ids)], dtype=self.FLOAT_TF) * self.irf_param_ranef_posterior_sd_init_unconstrained,
                        name=sn('%s_q_scale_%s_by_%s' % (param_name, '-'.join(ids), sn(ran_gf)))
                    )

                    param_q_dist = Normal(
                        loc=param_q_loc,
                        scale=self.constraint_fn(param_q_scale) + self.epsilon,
                        name=sn('%s_q_%s_by_%s' % (param_name, '-'.join(ids), sn(ran_gf)))
                    )

                    param = tf.cond(self.use_MAP_mode, param_q_dist.mean, param_q_dist.sample)

                    param_summary = param_q_dist.mean()

                    if self.declare_priors_ranef:
                        # Prior distribution
                        param_prior_dist = Normal(
                            loc=0.,
                            scale=self.irf_param_ranef_prior_sd_tf,
                            name='%s_by_%s' % (param_name, sn(ran_gf))
                        )
                        self.kl_penalties['%s_by_%s' % (param_name, sn(ran_gf))] = {
                            'loc': 0.,
                            'scale': self.irf_param_prior_sd * self.ranef_to_fixef_prior_sd_ratio,
                            'val': param_q_dist.kl_divergence(param_prior_dist)
                        }

                return param, param_summary

    def initialize_joint_distribution(self, means, sds, ran_gf=None):
        with self.sess.as_default():
            with self.sess.graph.as_default():
                dim = int(means.shape[0])

                # Posterior distribution
                joint_q_loc = tf.Variable(
                    tf.ones([dim], dtype=self.FLOAT_TF) * means,
                    name='joint_q_loc' if ran_gf is None else 'joint_q_loc_by_%s' % sn(ran_gf)
                )

                # Construct cholesky decomposition of initial covariance using sds, then use for initialization
                n_scale = int(dim * (dim + 1) / 2)
                if ran_gf is not None:
                    sds *= self.ranef_to_fixef_prior_sd_ratio
                cholesky = tf.diag(sds)
                tril_ix = np.ravel_multi_index(
                    np.tril_indices(dim),
                    (dim, dim)
                )
                scale_init = tf.gather(tf.reshape(cholesky, [dim * dim]), tril_ix)

                scale_posterior_init = scale_init * self.posterior_to_prior_sd_ratio

                joint_q_scale = tf.Variable(
                    tf.ones([n_scale], dtype=self.FLOAT_TF) * scale_posterior_init,
                    name='joint_q_scale' if ran_gf is None else 'joint_q_scale_by_%s' % sn(ran_gf)
                )

                joint_q_dist = MultivariateNormalTriL(
                    loc=joint_q_loc,
                    scale_tril=tf.contrib.distributions.fill_triangular(joint_q_scale),
                    name='joint_q' if ran_gf is None else 'joint_q_by_%s' % sn(ran_gf)
                )

                joint = tf.cond(self.use_MAP_mode, joint_q_dist.mean, joint_q_dist.sample)

                joint_summary = joint_q_dist.mean()

                if (ran_gf is None and self.declare_priors_fixef) or (ran_gf is not None and self.declare_priors_ranef):
                    # Prior distribution
                    joint_prior_dist = MultivariateNormalTriL(
                        loc=means,
                        scale_tril=tf.contrib.distributions.fill_triangular(scale_init),
                        name='joint' if ran_gf is None else 'joint_by_%s' % sn(ran_gf)
                    )
                    self.kl_penalties['joint' if ran_gf is None else 'joint_by_%s' % sn(ran_gf)] = {
                        'loc': 'multiple',
                        'scale': 'multiple',
                        'val': joint_q_dist.kl_divergence(joint_prior_dist)
                    }

                return joint, joint_summary

    def _initialize_output_model(self):
        with self.sess.as_default():
            with self.sess.graph.as_default():
                if self.y_sd_trainable:
                    y_sd_init_unconstrained = self.y_sd_init_unconstrained

                    # Posterior distribution
                    y_sd_loc_q = tf.Variable(
                        y_sd_init_unconstrained,
                        name='y_sd_loc_q'
                    )
                    y_sd_scale_q = tf.Variable(
                        self.y_sd_posterior_sd_init_unconstrained,
                        name='y_sd_scale_q'
                    )
                    y_sd_q_dist = Normal(
                        loc=y_sd_loc_q,
                        scale=self.constraint_fn(y_sd_scale_q) + self.epsilon,
                        name='y_sd_q'
                    )

                    y_sd = tf.cond(self.use_MAP_mode, y_sd_q_dist.mean, y_sd_q_dist.sample)

                    y_sd_summary = y_sd_q_dist.mean()

                    if self.declare_priors_fixef:
                        # Prior distribution
                        y_sd_prior_dist = Normal(
                            loc=y_sd_init_unconstrained,
                            scale=self.y_sd_prior_sd_tf,
                            name='y_sd'
                        )
                        self.kl_penalties['y_sd'] = {
                            'loc': self.y_sd_init,
                            'scale': self.y_sd_prior_sd,
                            'val': y_sd_q_dist.kl_divergence(y_sd_prior_dist)
                        }

                    y_sd = self.constraint_fn(y_sd) + self.epsilon
                    y_sd_summary = self.constraint_fn(y_sd_summary) + self.epsilon

                    tf.summary.scalar(
                        'error/y_sd',
                        y_sd_summary,
                        collections=['params']
                    )

                else:
                    stderr('Fixed y scale: %s\n' % self.y_sd_init)
                    y_sd = self.y_sd_init_tf
                    y_sd_summary = y_sd

                self.y_sd = y_sd
                self.y_sd_summary = y_sd_summary

                if self.asymmetric_error:
                    # Posterior distributions
                    y_skewness_loc_q = tf.Variable(
                        0.,
                        name='y_skewness_q_loc'
                    )
                    y_skewness_scale_q = tf.Variable(
                        self.y_skewness_posterior_sd_init_unconstrained,
                        name='y_skewness_q_loc'
                    )
                    self.y_skewness_q_dist = Normal(
                        loc=y_skewness_loc_q,
                        scale=self.constraint_fn(y_skewness_scale_q) + self.epsilon,
                        name='y_skewness_q'
                    )

                    self.y_skewness = tf.cond(self.use_MAP_mode, self.y_skewness_q_dist.mean, self.y_skewness_q_dist.sample)

                    self.y_skewness_summary = self.y_skewness_q_dist.mean()

                    tf.summary.scalar(
                        'error/y_skewness_summary',
                        self.y_skewness_summary,
                        collections=['params']
                    )

                    y_tailweight_loc_q = tf.Variable(
                        self.y_tailweight_posterior_loc_init_unconstrained,
                        name='y_tailweight_q_loc'
                    )
                    y_tailweight_scale_q = tf.Variable(
                        self.y_tailweight_posterior_sd_init_unconstrained,
                        name='y_tailweight_q_scale'
                    )
                    self.y_tailweight_q_dist = Normal(
                        loc=y_tailweight_loc_q,
                        scale=self.constraint_fn(y_tailweight_scale_q) + self.epsilon,
                        name='y_tailweight_q'
                    )

                    self.y_tailweight = tf.cond(self.use_MAP_mode, self.y_tailweight_q_dist.mean, self.y_tailweight_q_dist.sample)

                    self.y_tailweight_summary = self.y_tailweight_q_dist.mean()
                    tf.summary.scalar(
                        'error/y_tailweight',
                        self.constraint_fn(self.y_tailweight_summary) + self.epsilon,
                        collections=['params']
                    )

                    if self.declare_priors_fixef:
                        # Prior distributions
                        self.y_skewness_prior_dist = Normal(
                            loc=0.,
                            scale=self.y_skewness_prior_sd_tf,
                            name='y_skewness'
                        )
                        self.y_tailweight_prior_dist = Normal(
                            loc=self.y_tailweight_prior_loc_unconstrained,
                            scale=self.y_tailweight_prior_sd_tf,
                            name='y_tailweight'
                        )
                        self.kl_penalties['y_skewness'] = {
                            'loc': 0.,
                            'scale': self.y_skewness_prior_dist,
                            'val': self.y_skewness_q_dist.kl_divergence(self.y_skewness_prior_dist)
                        }
                        self.kl_penalties['y_tailweight'] = {
                            'loc': 1.,
                            'scale': self.y_tailweight_prior_dist,
                            'val': self.y_tailweight_q_dist.kl_divergence(self.y_tailweight_prior_dist)
                        }

                    if self.standardize_response:
                        self.out_standardized_dist = SinhArcsinh(
                            loc=self.out,
                            scale=y_sd,
                            skewness=self.y_skewness,
                            tailweight=self.constraint_fn(self.y_tailweight) + self.epsilon,
                            name='output_standardized'
                        )
                        self.out_standardized = tf.cond(
                            self.use_MAP_mode,
                            self.out_standardized_dist.mean,
                            self.out_standardized_dist.sample
                        )
                        self.err_dist_standardized_dist = SinhArcsinh(
                            loc=0.,
                            scale=y_sd,
                            skewness=self.y_skewness,
                            tailweight=self.constraint_fn(self.y_tailweight) + self.epsilon,
                            name='err_dist_standardized'
                        )
                        self.err_dist_standardized_summary = SinhArcsinh(
                            loc=0.,
                            scale=y_sd_summary,
                            skewness=self.y_skewness_summary,
                            tailweight=self.constraint_fn(self.y_tailweight_summary) + self.epsilon,
                            name='err_dist_standardized_summary'
                        )

                        self.out_dist = SinhArcsinh(
                            loc=self.out * self.y_train_sd + self.y_train_mean,
                            scale=y_sd * self.y_train_sd,
                            skewness=self.y_skewness,
                            tailweight=self.constraint_fn(self.y_tailweight) + self.epsilon,
                            name='output_dist'
                        )
                        self.out = tf.cond(
                            self.use_MAP_mode,
                            self.out_dist.mean,
                            self.out_dist.sample
                        )

                        self.err_dist = SinhArcsinh(
                            loc=0.,
                            scale=y_sd * self.y_train_sd,
                            skewness=self.y_skewness,
                            tailweight=self.constraint_fn(self.y_tailweight) + self.epsilon,
                            name='err_dist'
                        )
                        self.err = tf.cond(
                            self.use_MAP_mode,
                            self.err_dist.mean,
                            self.err_dist.sample
                        )
                        self.err_dist_summary = SinhArcsinh(
                            loc=0.,
                            scale=y_sd_summary * self.y_train_sd,
                            skewness=self.y_skewness_summary,
                            tailweight=self.constraint_fn(self.y_tailweight_summary) + self.epsilon,
                            name='err_dist_summary'
                        )
                    else:
                        self.out_dist = SinhArcsinh(
                            loc=self.out,
                            scale=y_sd,
                            skewness=self.y_skewness,
                            tailweight=self.constraint_fn(self.y_tailweight) + self.epsilon,
                            name='output_dist'
                        )
                        self.out = tf.cond(
                            self.use_MAP_mode,
                            self.out_dist.mean,
                            self.out_dist.sample
                        )

                        self.err_dist = SinhArcsinh(
                            loc=0.,
                            scale=y_sd,
                            skewness=self.y_skewness,
                            tailweight=self.constraint_fn(self.y_tailweight) + self.epsilon,
                            name='err_dist'
                        )
                        self.err = tf.cond(
                            self.use_MAP_mode,
                            self.err_dist.mean,
                            self.err_dist.sample
                        )
                        self.err_dist_summary = SinhArcsinh(
                            loc=0.,
                            scale=y_sd_summary,
                            skewness=self.y_skewness_summary,
                            tailweight=self.constraint_fn(self.y_tailweight_summary) + self.epsilon,
                            name='err_dist_summary'
                        )

                else:
                    if self.standardize_response:
                        self.out_standardized_dist = Normal(
                            loc=self.out,
                            scale=self.y_sd,
                            name='output_standardized'
                        )
                        self.out_standardized = tf.cond(
                            self.use_MAP_mode,
                            self.out_standardized_dist.mean,
                            self.out_standardized_dist.sample
                        )

                        self.err_dist_standardized = Normal(
                            loc=0.,
                            scale=self.y_sd,
                            name='err_dist_standardized'
                        )
                        self.err_standardized = tf.cond(
                            self.use_MAP_mode,
                            self.out_standardized_dist.mean,
                            self.out_standardized_dist.sample
                        )
                        self.err_dist_standardized_summary = Normal(
                            loc=0.,
                            scale=self.y_sd_summary,
                            name='err_dist_standardized_summary'
                        )

                        self.out_dist = Normal(
                            loc=self.out * self.y_train_sd + self.y_train_mean,
                            scale=self.y_sd * self.y_train_sd,
                            name='output'
                        )
                        self.out = tf.cond(
                            self.use_MAP_mode,
                            self.out_dist.mean,
                            self.out_dist.sample
                        )

                        self.err_dist = Normal(
                            loc=0.,
                            scale=self.y_sd * self.y_train_sd,
                            name='err_dist'
                        )
                        self.err = tf.cond(
                            self.use_MAP_mode,
                            self.err_dist.mean,
                            self.err_dist.sample
                        )
                        self.err_dist_summary = Normal(
                            loc=0.,
                            scale=self.y_sd_summary * self.y_train_sd,
                            name='err_dist_summary'
                        )
                    else:
                        self.out_dist = Normal(
                            loc=self.out,
                            scale=self.y_sd,
                            name='output'
                        )
                        self.out = tf.cond(
                            self.use_MAP_mode,
                            self.out_dist.mean,
                            self.out_dist.sample
                        )

                        self.err_dist = Normal(
                            loc=0.,
                            scale=self.y_sd,
                            name='err_dist'
                        )
                        self.err = tf.cond(
                            self.use_MAP_mode,
                            self.err_dist.mean,
                            self.err_dist.sample
                        )
                        self.err_dist_summary = Normal(
                            loc=0.,
                            scale=self.y_sd_summary,
                            name='err_dist_summary'
                        )

                self.err_dist_plot = tf.exp(self.err_dist.log_prob(self.support[None,...]))
                self.err_dist_plot_summary = tf.exp(self.err_dist_summary.log_prob(self.support[None,...]))
                self.err_dist_lb = self.err_dist_summary.quantile(.025)
                self.err_dist_ub = self.err_dist_summary.quantile(.975)

                empirical_quantiles = tf.linspace(0., 1., self.n_errors)
                if self.standardize_response:
                    self.err_dist_standardized_theoretical_quantiles = self.err_dist_standardized.quantile(empirical_quantiles)
                    self.err_dist_standardized_theoretical_cdf = self.err_dist_standardized.cdf(self.errors)
                    self.err_dist_standardized_summary_theoretical_quantiles = self.err_dist_standardized_summary.quantile(empirical_quantiles)
                    self.err_dist_standardized_summary_theoretical_cdf = self.err_dist_standardized_summary.cdf(self.errors)
                self.err_dist_theoretical_quantiles = self.err_dist.quantile(empirical_quantiles)
                self.err_dist_theoretical_cdf = self.err_dist.cdf(self.errors)
                self.err_dist_summary_theoretical_quantiles = self.err_dist_summary.quantile(empirical_quantiles)
                self.err_dist_summary_theoretical_cdf = self.err_dist_summary.cdf(self.errors)

                self.ll = self.out_dist.log_prob(self.y)
                if self.standardize_response:
                    self.X_conv_standardized_scaled = self.X_conv_scaled
                    self.X_conv_scaled *= self.y_train_sd

                    y_standardized = (self.y - self.y_train_mean) / self.y_train_sd
                    self.ll_standardized = self.out_standardized_dist.log_prob(y_standardized)

    def initialize_objective(self):
        with self.sess.as_default():
            with self.sess.graph.as_default():
                self._initialize_output_model()

                if self.standardize_response:
                    loss_func = -self.ll_standardized
                else:
                    loss_func = -self.ll

                self.loss_func, self.reg_loss, self.kl_loss = self._process_objective(loss_func)

                self.optim = self._initialize_optimizer()

                self.train_op = self.optim.minimize(self.loss_func, global_step=self.global_batch_step)

    # Overload this method to perform parameter sampling and compute credible intervals
    def _extract_parameter_values(self, fixed=True, level=95, n_samples=None):
        if n_samples is None:
            n_samples = self.n_samples_eval

        alpha = 100 - float(level)

        with self.sess.as_default():
            with self.sess.graph.as_default():
                if fixed:
                    param_vector = self.parameter_table_fixed_values
                else:
                    param_vector = self.parameter_table_random_values

            samples = [self.sess.run(param_vector, feed_dict={self.use_MAP_mode: False}) for _ in range(n_samples)]
            samples = np.stack(samples, axis=1)

            mean = samples.mean(axis=1)
            lower = np.percentile(samples, alpha / 2, axis=1)
            upper = np.percentile(samples, 100 - (alpha / 2), axis=1)

            out = np.stack([mean, lower, upper], axis=1)

            return out




    #####################################################
    #
    #  Public methods
    #
    ######################################################

    def report_settings(self, indent=0):
        out = super(CDRBayes, self).report_settings(indent=indent)
        for kwarg in CDRBAYES_INITIALIZATION_KWARGS:
            val = getattr(self, kwarg.key)
            out += ' ' * indent + '  %s: %s\n' %(kwarg.key, "\"%s\"" %val if isinstance(val, str) else val)

        out += '\n'

        return out

    def report_regularized_variables(self, indent=0):
        """
        Generate a string representation of the model's regularization structure.

        :param indent: ``int``; indentation level
        :return: ``str``; the regularization report
        """
        with self.sess.as_default():
            with self.sess.graph.as_default():
                out = super(CDRBayes, self).report_regularized_variables(indent)

                out += ' ' * indent + 'VARIATIONAL PRIORS:\n'

                kl_penalties = self.kl_penalties

                if len(kl_penalties) == 0:
                    out +=  ' ' * indent + '  No variational priors.\n\n'
                else:
                    for name in sorted(list(kl_penalties.keys())):
                        out += ' ' * indent + '  %s:\n' % name
                        for k in sorted(list(kl_penalties[name].keys())):
                            if not k == 'val':
                                out += ' ' * indent + '    %s: %s\n' % (k, kl_penalties[name][k])

                    out += '\n'

                return out

    def run_predict_op(self, feed_dict, standardize_response=False, n_samples=None, algorithm='MAP', verbose=True):
        use_MAP_mode =  algorithm in ['map', 'MAP']
        feed_dict[self.use_MAP_mode] = use_MAP_mode

        if standardize_response and self.standardize_response:
            out = self.out_standardized
        else:
            out = self.out

        with self.sess.as_default():
            with self.sess.graph.as_default():
                if use_MAP_mode:
                    preds = self.sess.run(out, feed_dict=feed_dict)
                else:
                    if n_samples is None:
                        n_samples = self.n_samples_eval

                    if verbose:
                        pb = tf.contrib.keras.utils.Progbar(n_samples)

                    preds = np.zeros((len(feed_dict[self.time_y]), n_samples))

                    for i in range(n_samples):
                        preds[:, i] = self.sess.run(out, feed_dict=feed_dict)
                        if verbose:
                            pb.update(i + 1, force=True)

                    preds = preds.mean(axis=1)

                return preds

    def run_loglik_op(self, feed_dict, standardize_response=False, n_samples=None, algorithm='MAP', verbose=True):
        use_MAP_mode =  algorithm in ['map', 'MAP']
        feed_dict[self.use_MAP_mode] = use_MAP_mode

        if standardize_response and self.standardize_response:
            ll = self.ll_standardized
        else:
            ll = self.ll

        with self.sess.as_default():
            with self.sess.graph.as_default():
                if use_MAP_mode:
                    log_lik = self.sess.run(ll, feed_dict=feed_dict)
                else:
                    if n_samples is None:
                        n_samples = self.n_samples_eval

                    if verbose:
                        pb = tf.contrib.keras.utils.Progbar(n_samples)

                    log_lik = np.zeros((len(feed_dict[self.time_y]), n_samples))

                    for i in range(n_samples):
                        log_lik[:, i] = self.sess.run(ll, feed_dict=feed_dict)
                        if verbose:
                            pb.update(i + 1, force=True)

                    log_lik = log_lik.mean(axis=1)

                return log_lik

    def run_loss_op(self, feed_dict, n_samples=None, algorithm='MAP', verbose=True):
        use_MAP_mode =  algorithm in ['map', 'MAP']
        feed_dict[self.use_MAP_mode] = use_MAP_mode

        with self.sess.as_default():
            with self.sess.graph.as_default():
                if use_MAP_mode:
                    loss = self.sess.run(self.loss_func, feed_dict=feed_dict)
                else:
                    if n_samples is None:
                        n_samples = self.n_samples_eval

                    if verbose:
                        pb = tf.contrib.keras.utils.Progbar(n_samples)

                    loss = np.zeros((len(feed_dict[self.time_y]), n_samples))

                    for i in range(n_samples):
                        loss[:, i] = self.sess.run(self.loss_func, feed_dict=feed_dict)
                        if verbose:
                            pb.update(i + 1, force=True)

                    loss = loss.mean()

                return loss

    def run_conv_op(self, feed_dict, scaled=False, standardize_response=False, n_samples=None, algorithm='MAP', verbose=True):
        use_MAP_mode =  algorithm in ['map', 'MAP']
        feed_dict[self.use_MAP_mode] = use_MAP_mode

        if scaled:
            if standardize_response and self.standardize_response:
                X_conv = self.X_conv_standardized
            else:
                X_conv = self.X_conv_scaled
        else:
            X_conv = self.X_conv

        with self.sess.as_default():
            with self.sess.graph.as_default():
                if use_MAP_mode:
                    X_conv = self.sess.run(X_conv, feed_dict=feed_dict)
                else:
                    if n_samples is None:
                        n_samples = self.n_samples_eval
                    if verbose:
                        pb = tf.contrib.keras.utils.Progbar(n_samples)

                    X_conv = np.zeros((len(feed_dict[self.X]), self.X_conv.shape[-1], n_samples))

                    for i in range(0, n_samples):
                        X_conv[..., i] = self.sess.run(X_conv, feed_dict=feed_dict)
                        if verbose:
                            pb.update(i + 1, force=True)

                    X_conv = X_conv.mean(axis=2)

                return X_conv

    def finalize(self):
        super(CDRBayes, self).finalize()



