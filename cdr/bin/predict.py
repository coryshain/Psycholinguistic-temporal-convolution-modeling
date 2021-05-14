import argparse
import os
import sys
import re
import pickle
import numpy as np
import pandas as pd

pd.options.mode.chained_assignment = None

from cdr.config import Config
from cdr.io import read_data
from cdr.formula import Formula
from cdr.data import add_dv, filter_invalid_responses, preprocess_data, compute_splitID, compute_partition, get_first_last_obs_lists, s, c, z
from cdr.util import mse, mae, percent_variance_explained
from cdr.util import load_cdr, filter_models, get_partition_list, paths_from_partition_cliarg, stderr
from cdr.plot import plot_qq


spillover = re.compile('(z_)?([^ (),]+)S([0-9]+)')


# These code blocks are factored out because they are used by both LM/E objects and CDR objects under 2-step analysis
def predict_LM(model_path, outdir, X, y, dv, partition_name, model_name=''):
    stderr('Retrieving saved model %s...\n' % m)
    with open(model_path, 'rb') as m_file:
        lm = pickle.load(m_file)

    lm_preds = lm.predict(X)
    with open(outdir + '/' + model_name + '/%spreds_%s.txt' % ('' if model_name=='' else model_name + '_', partition_name), 'w') as p_file:
        for i in range(len(lm_preds)):
            p_file.write(str(lm_preds[i]) + '\n')
    losses = np.array(y[dv] - lm_preds) ** 2
    with open(outdir + '/' + model_name + '/%s_losses_mse_%s.txt' % ('' if model_name=='' else model_name + '_', partition_name), 'w') as p_file:
        for i in range(len(losses)):
            p_file.write(str(losses[i]) + '\n')
    lm_mse = mse(y[dv], lm_preds)
    lm_mae = mae(y[dv], lm_preds)
    summary = '=' * 50 + '\n'
    summary += 'Linear regression\n\n'
    summary += 'Model name: %s\n\n' % model_name
    summary += 'Formula:\n'
    summary += '  ' + formula + '\n'
    summary += str(lm.summary()) + '\n'
    summary += 'Loss (%s set):\n' % partition_name
    summary += '  MSE: %.4f\n' % lm_mse
    summary += '  MAE: %.4f\n' % lm_mae
    summary += '=' * 50 + '\n'
    with open(outdir + '/%seval_%s.txt' % ('' if model_name=='' else model_name + '_', partition_name), 'w') as f_out:
        f_out.write(summary)
    stderr(summary)

def predict_LME(model_path, outdir, X, y, dv, partition_name, model_name=''):
    stderr('Retrieving saved model %s...\n' % m)
    with open(model_path, 'rb') as m_file:
        lme = pickle.load(m_file)

    summary = '=' * 50 + '\n'
    summary += 'LME regression\n\n'
    summary += 'Model name: %s\n\n' % m
    summary += 'Formula:\n'
    summary += '  ' + formula + '\n'
    summary += str(lme.summary()) + '\n'

    if args.mode in [None, 'response']:
        lme_preds = lme.predict(X)

        with open(outdir + '/%spreds_%s.txt' % ('' if model_name=='' else model_name + '_', partition_name), 'w') as p_file:
            for i in range(len(lme_preds)):
                p_file.write(str(lme_preds[i]) + '\n')
        losses = np.array(y[dv] - lme_preds) ** 2
        with open(outdir + '/%slosses_mse_%s.txt' % ('' if model_name=='' else model_name + '_', partition_name), 'w') as p_file:
            for i in range(len(losses)):
                p_file.write(str(losses[i]) + '\n')
        lme_mse = mse(y[dv], lme_preds)
        lme_mae = mae(y[dv], lme_preds)

        summary += 'Loss (%s set):\n' % partition_name
        summary += '  MSE: %.4f\n' % lme_mse
        summary += '  MAE: %.4f\n' % lme_mae

    summary += '=' * 50 + '\n'
    with open(outdir + '/%seval_%s.txt' % ('' if model_name=='' else model_name + '_', partition_name), 'w') as f_out:
        f_out.write(summary)
    stderr(summary)


if __name__ == '__main__':
    argparser = argparse.ArgumentParser('''
        Generates predictions from data given saved model(s)
    ''')
    argparser.add_argument('config_path', help='Path to configuration (*.ini) file')
    argparser.add_argument('-m', '--models', nargs='*', default=[], help='List of model names from which to predict. Regex permitted. If unspecified, predicts from all models.')
    argparser.add_argument('-d', '--data', nargs='+', default=[], help='Pairs of paths or buffers <predictors, responses>, allowing prediction on arbitrary evaluation data. Predictors may consist of ``;``-delimited paths designating files containing predictors with different timestamps. Within each file, timestamps are treated as identical across predictors.')
    argparser.add_argument('-p', '--partition', nargs='+', default=['dev'], help='List of names of partitions to use ("train", "dev", "test", or hyphen-delimited subset of these).')
    argparser.add_argument('-z', '--standardize_response', action='store_true', help='Standardize (Z-transform) response. Ignored for non-CDR models, and ignored for CDR models unless fitting used setting ``standardize_respose=True``.')
    argparser.add_argument('-n', '--nsamples', type=int, default=1024, help='Number of posterior samples to average (only used for CDRBayes)')
    argparser.add_argument('-M', '--mode', nargs='+', default=None, help='Predict mode(s) (set of "response", "loglik", and/or "loss") or default ``None``, which does both "response" and "loglik". Modes "loglik" and "loss" are only valid for CDR.')
    argparser.add_argument('-a', '--algorithm', type=str, default='MAP', help='Algorithm ("sampling" or "MAP") to use for extracting predictions from CDRBayes. Ignored for CDRMLE.')
    argparser.add_argument('-t', '--twostep', action='store_true', help='For CDR models, predict from fitted LME model from two-step hypothesis test.')
    argparser.add_argument('-A', '--ablated_models', action='store_true', help='For two-step prediction from CDR models, predict from data convolved using the ablated model. Otherwise predict from data convolved using the full model.')
    argparser.add_argument('-e', '--extra_cols', action='store_true', help='For prediction from CDR models, dump prediction outputs and response metadata to a single csv.')
    args, unknown = argparser.parse_known_args()

    p = Config(args.config_path)

    models = filter_models(p.model_list, args.models)

    run_baseline = False
    run_cdr = False
    for m in models:
        if not run_baseline and m.startswith('LM') or m.startswith('GAM'):
            run_baseline = True
        elif not run_cdr and (m.startswith('CDR') or m.startswith('DTSR')):
            run_cdr = True

    cdr_formula_list = [Formula(p.models[m]['formula']) for m in models if (m.startswith('CDR') or m.startswith('DTSR'))]
    cdr_formula_name_list = [m for m in p.model_list if (m.startswith('CDR') or m.startswith('DTSR'))]

    evaluation_sets = []
    evaluation_set_partitions = []
    evaluation_set_names = []
    evaluation_set_paths = []

    for p_name in args.partition:
        partitions = get_partition_list(p_name)
        partition_str = '-'.join(partitions)
        X_paths, y_paths = paths_from_partition_cliarg(partitions, p)
        X, y = read_data(X_paths, y_paths, p.series_ids, categorical_columns=list(set(p.split_ids + p.series_ids + [v for x in cdr_formula_list for v in x.rangf])))
        X, y, select, X_response_aligned_predictor_names, X_response_aligned_predictors, X_2d_predictor_names, X_2d_predictors = preprocess_data(
            X,
            y,
            cdr_formula_list,
            p.series_ids,
            filters=p.filters,
            compute_history=run_cdr,
            history_length=p.history_length
        )
        evaluation_sets.append((X, y, select, X_response_aligned_predictor_names, X_response_aligned_predictors, X_2d_predictor_names, X_2d_predictors))
        evaluation_set_partitions.append(partitions)
        evaluation_set_names.append(partition_str)
        evaluation_set_paths.append((X_paths, y_paths))

    assert len(args.data) % 2 == 0, 'Argument ``data`` must be a list with an even number of elements.'
    for i in range(0, len(args.data), 2):
        partition_str = '%d' % (int(i / 2) + 1)
        X_paths, y_paths = args.data[i:i+2]
        X, y = read_data(X_paths, y_paths, p.series_ids, categorical_columns=list(set(p.split_ids + p.series_ids + [v for x in cdr_formula_list for v in x.rangf])))
        X, y, select, X_response_aligned_predictor_names, X_response_aligned_predictors, X_2d_predictor_names, X_2d_predictors = preprocess_data(
            X,
            y,
            cdr_formula_list,
            p.series_ids,
            filters=p.filters,
            compute_history=run_cdr,
            history_length=p.history_length
        )
        evaluation_sets.append((X, y, select, X_response_aligned_predictor_names, X_response_aligned_predictors, X_2d_predictor_names, X_2d_predictors))
        evaluation_set_partitions.append(None)
        evaluation_set_names.append(partition_str)
        evaluation_set_paths.append((X_paths, y_paths))

    if run_baseline:
        from cdr.baselines import py2ri
        evaluation_set_baselines = []
        partition_name_to_ix = {'train': 0, 'dev': 1, 'test': 2}
        for i in range(len(evaluation_sets)):
            X, y, select = evaluation_sets[i][:3]
            assert len(X) == 1, 'Cannot run baselines on asynchronously sampled predictors'
            X_cur = X[0]
            partitions = evaluation_set_partitions[i]
            X_cur['splitID'] = compute_splitID(X_cur, p.split_ids)

            X_baseline = X_cur

            for m in models:
                if not m in cdr_formula_name_list:
                    p.set_model(m)
                    form = p['formula']
                    lhs, rhs = form.split('~')
                    preds = rhs.split('+')
                    for pred in preds:
                        sp = spillover.search(pred)
                        if sp and sp.group(2) in X_baseline.columns:
                            x_id = sp.group(2)
                            n = int(sp.group(3))
                            x_id_sp = x_id + 'S' + str(n)
                            if x_id_sp not in X_baseline.columns:
                                X_baseline[x_id_sp] = X_baseline.groupby(p.series_ids)[x_id].shift(n, fill_value=0.)

            if partitions is not None:
                part = compute_partition(X_cur, p.modulus, 3)
                part_select = None
                for partition in partitions:
                    if part_select is None:
                        part_select = part[partition_name_to_ix[partition]]
                    else:
                        part_select &= part[partition_name_to_ix[partition]]

                X_baseline = X_baseline[part_select]

            if p.merge_cols is None:
                merge_cols = sorted(list(set(X_baseline.columns) & set(y.columns)))
            else:
                merge_cols = p.merge_cols
            X_baseline = pd.merge(X_baseline, y, on=merge_cols, how='inner')

            for m in models:
                if not m in cdr_formula_name_list:
                    p.set_model(m)
                    form = p['formula']
                    lhs, rhs = form.split('~')
                    dv = lhs.strip()
                    y = add_dv(dv, y)
                    if not dv in X_baseline.columns:
                        X_baseline[dv] = y[dv]

            for c in X_baseline.columns:
                if X_baseline[c].dtype.name == 'category':
                    X_baseline[c] = X_baseline[c].astype(str)

            X_baseline = py2ri(X_baseline)
            evaluation_set_baselines.append(X_baseline)

    for d in range(len(evaluation_sets)):
        X, y, select, X_response_aligned_predictor_names, X_response_aligned_predictors, X_2d_predictor_names, X_2d_predictors = evaluation_sets[d]
        partition_str = evaluation_set_names[d]
        if run_baseline:
            X_baseline = evaluation_set_baselines[d]

        for m in models:
            formula = p.models[m]['formula']
            p.set_model(m)
            if not os.path.exists(p.outdir + '/' + m):
                os.makedirs(p.outdir + '/' + m)
            with open(p.outdir + '/' + m + '/pred_inputs_%s.txt' % partition_str, 'w') as f:
                f.write('%s\n' % (' '.join(evaluation_set_paths[d][0])))
                f.write('%s\n' % (' '.join(evaluation_set_paths[d][1])))

            if m.startswith('LME'):
                dv = formula.strip().split('~')[0].strip()

                predict_LME(
                    p.outdir + '/' + m + '/m.obj',
                    p.outdir + '/' + m,
                    X_baseline,
                    y,
                    dv,
                    partition_str
                )

            elif m.startswith('LM'):
                dv = formula.strip().split('~')[0].strip()

                predict_LM(
                    p.outdir + '/' + m + '/m.obj',
                    p.outdir + '/' + m,
                    X_baseline,
                    y,
                    dv,
                    partition_str
                )

            elif m.startswith('GAM'):
                import re
                dv = formula.strip().split('~')[0].strip()

                ## For some reason, GAM can't predict using custom functions, so we have to translate them
                z_term = re.compile('z.\((.*)\)')
                c_term = re.compile('c.\((.*)\)')
                formula = [t.strip() for t in formula.strip().split() if t.strip() != '']
                for i in range(len(formula)):
                    formula[i] = z_term.sub(r'scale(\1)', formula[i])
                    formula[i] = c_term.sub(r'scale(\1, scale=FALSE)', formula[i])
                formula = ' '.join(formula)

                stderr('Retrieving saved model %s...\n' % m)
                with open(p.outdir + '/' + m + '/m.obj', 'rb') as m_file:
                    gam = pickle.load(m_file)
                gam_preds = gam.predict(X_baseline)
                with open(p.outdir + '/' + m + '/preds_%s.txt' % partition_str, 'w') as p_file:
                    for i in range(len(gam_preds)):
                        p_file.write(str(gam_preds[i]) + '\n')
                losses = np.array(y[dv] - gam_preds) ** 2
                with open(p.outdir + '/' + m + '/losses_mse_%s.txt' % partition_str, 'w') as p_file:
                    for i in range(len(losses)):
                        p_file.write(str(losses[i]) + '\n')
                gam_mse = mse(y[dv], gam_preds)
                gam_mae = mae(y[dv], gam_preds)
                summary = '=' * 50 + '\n'
                summary += 'GAM regression\n\n'
                summary += 'Model name: %s\n\n' % m
                summary += 'Formula:\n'
                summary += '  ' + formula + '\n'
                summary += str(gam.summary()) + '\n'
                summary += 'Loss (%s set):\n' % partition_str
                summary += '  MSE: %.4f\n' % gam_mse
                summary += '  MAE: %.4f\n' % gam_mae
                summary += '=' * 50 + '\n'
                with open(p.outdir + '/' + m + '/eval_%s.txt' % partition_str, 'w') as f_out:
                    f_out.write(summary)
                stderr(summary)

            elif (m.startswith('CDR') or m.startswith('DTSR')):
                if not p.use_gpu_if_available:
                    os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

                dv = formula.strip().split('~')[0].strip()
                y_valid, select_y_valid = filter_invalid_responses(y, dv)
                X_response_aligned_predictors_valid = X_response_aligned_predictors
                if X_response_aligned_predictors_valid is not None:
                    X_response_aligned_predictors_valid = X_response_aligned_predictors_valid[select_y_valid]

                if args.twostep:
                    from cdr.baselines import py2ri

                    if args.ablated_models:
                        data_path = p.outdir + '/' + m + '/X_conv_' + partition_str + '.csv'
                    else:
                        data_path = p.outdir + '/' + m.split('!')[0] + '/X_conv_' + partition_str + '.csv'

                    df = pd.read_csv(data_path, sep=' ', skipinitialspace=True)
                    for c in df.columns:
                        if df[c].dtype.name == 'object':
                            df[c] = df[c].astype(str)

                    new_cols = []
                    for c in df.columns:
                        new_cols.append(c.replace('-', '_'))
                    df.columns = new_cols

                    df_r = py2ri(df)

                    is_lme = '|' in Formula(p['formula']).to_lmer_formula_string()

                    if is_lme:
                        predict_LME(
                            p.outdir + '/' + m + '/lm_train.obj',
                            p.outdir + '/' + m,
                            df_r,
                            df,
                            dv,
                            partition_str,
                            model_name='LM_2STEP'
                        )
                    else:
                        predict_LM(
                            p.outdir + '/' + m + '/lm_train.obj',
                            p.outdir + '/' + m,
                            df_r,
                            df,
                            dv,
                            partition_str,
                            model_name='LM_2STEP'
                        )

                else:
                    stderr('Retrieving saved model %s...\n' % m)
                    cdr_model = load_cdr(p.outdir + '/' + m)

                    bayes = p['network_type'] == 'bayes'

                    summary = '=' * 50 + '\n'
                    summary += 'CDR regression\n\n'
                    summary += 'Model name: %s\n\n' % m
                    summary += 'Formula:\n'
                    summary += '  ' + formula + '\n\n'
                    summary += 'Partition: %s\n\n' % partition_str

                    cdr_mse = cdr_mae = cdr_loglik = cdr_loss = cdr_percent_variance_explained = cdr_true_variance = None

                    if cdr_model.standardize_response and args.standardize_response:
                        y_cur = (y_valid[dv] - cdr_model.y_train_mean) / cdr_model.y_train_sd
                    else:
                        y_cur = y_valid[dv]
                    if args.mode is None or 'response' in args.mode:
                        first_obs, last_obs = get_first_last_obs_lists(y_valid)
                        cdr_preds = cdr_model.predict(
                            X,
                            y_valid.time,
                            y_valid[cdr_model.form.rangf],
                            first_obs,
                            last_obs,
                            X_response_aligned_predictor_names=X_response_aligned_predictor_names,
                            X_response_aligned_predictors=X_response_aligned_predictors_valid,
                            X_2d_predictor_names=X_2d_predictor_names,
                            X_2d_predictors=X_2d_predictors,
                            n_samples=args.nsamples,
                            algorithm=args.algorithm,
                            standardize_response=args.standardize_response
                        )

                        losses = np.array(y_cur - cdr_preds) ** 2

                        if args.extra_cols:
                            df_out = pd.DataFrame(
                                {
                                    'CDRlossMSE': losses,
                                    'CDRpreds': cdr_preds,
                                    'yStandardized': y_cur
                                }
                            )
                            df_out = pd.concat([y_valid.reset_index(drop=True), df_out.reset_index(drop=True)], axis=1)
                        else:
                            preds_outfile = p.outdir + '/' + m + '/preds_%s.txt' % partition_str
                            loss_outfile = p.outdir + '/' + m + '/losses_mse_%s.txt' % partition_str
                            obs_outfile = p.outdir + '/' + m + '/obs_%s.txt' % partition_str

                            with open(preds_outfile, 'w') as p_file:
                                for i in range(len(cdr_preds)):
                                    p_file.write(str(cdr_preds[i]) + '\n')
                            with open(loss_outfile, 'w') as l_file:
                                for i in range(len(losses)):
                                    l_file.write(str(losses[i]) + '\n')
                            with open(obs_outfile, 'w') as p_file:
                                for i in range(len(y_cur)):
                                    p_file.write(str(y_cur.iloc[i]) + '\n')

                        cdr_mse = mse(y_cur, cdr_preds)
                        cdr_mae = mae(y_cur, cdr_preds)
                        cdr_percent_variance_explained = percent_variance_explained(y_cur, cdr_preds)
                        cdr_true_variance = np.std(y_cur) ** 2
                        y_dv_mean = y_cur.mean()

                        err = np.sort(y_cur - cdr_preds)
                        err_theoretical_q = cdr_model.error_theoretical_quantiles(len(err))
                        valid = np.isfinite(err_theoretical_q)
                        err = err[valid]
                        err_theoretical_q = err_theoretical_q[valid]

                        plot_qq(
                            err_theoretical_q,
                            err,
                            dir=cdr_model.outdir,
                            filename='error_qq_plot_%s.png' % partition_str,
                            xlab='Theoretical',
                            ylab='Empirical'
                        )

                        D, p_value = cdr_model.error_ks_test(err)

                    if args.mode is None or 'loglik' in args.mode:
                        cdr_loglik_vector = cdr_model.log_lik(
                            X,
                            y_valid,
                            X_response_aligned_predictor_names=X_response_aligned_predictor_names,
                            X_response_aligned_predictors=X_response_aligned_predictors_valid,
                            X_2d_predictor_names=X_2d_predictor_names,
                            X_2d_predictors=X_2d_predictors,
                            n_samples=args.nsamples,
                            algorithm=args.algorithm,
                            standardize_response=args.standardize_response
                        )

                        if args.extra_cols:
                            df_ll = pd.DataFrame({'CDRloglik': cdr_loglik_vector})
                            df_out= pd.concat([df_out, df_ll], axis=1)
                        else:
                            ll_outfile = p.outdir + '/' + m + '/loglik_%s.txt' % partition_str
                            with open(ll_outfile, 'w') as l_file:
                                for i in range(len(cdr_loglik_vector)):
                                    l_file.write(str(cdr_loglik_vector[i]) + '\n')
                        cdr_loglik = cdr_loglik_vector.sum()
                    if args.mode is not None and 'loss' in args.mode:
                        cdr_loss = cdr_model.loss(
                            X,
                            y_valid,
                            X_response_aligned_predictor_names=X_response_aligned_predictor_names,
                            X_response_aligned_predictors=X_response_aligned_predictors_valid,
                            X_2d_predictor_names=X_2d_predictor_names,
                            X_2d_predictors=X_2d_predictors,
                            n_samples=args.nsamples,
                            algorithm=args.algorithm
                        )

                    if bayes:
                        if cdr_model.pc:
                            terminal_names = cdr_model.src_terminal_names
                        else:
                            terminal_names = cdr_model.terminal_names

                    if args.extra_cols:
                        preds_outfile = p.outdir + '/' + m + '/preds_table_%s.csv' % partition_str
                        df_out.to_csv(preds_outfile, sep=' ', na_rep='NaN', index=False)

                    summary += 'Training iterations completed: %d\n\n' % cdr_model.global_step.eval(session=cdr_model.sess)

                    summary += cdr_model.report_evaluation(
                        mse=cdr_mse,
                        mae=cdr_mae,
                        loglik=cdr_loglik,
                        loss=cdr_loss,
                        percent_variance_explained=cdr_percent_variance_explained,
                        true_variance=cdr_true_variance,
                        ks_results=(D, p_value) if args.mode in [None, 'response'] else None
                    )

                    summary += '=' * 50 + '\n'

                    with open(p.outdir + '/' + m + '/eval_%s.txt' % partition_str, 'w') as f_out:
                        f_out.write(summary)
                    stderr(summary)
                    stderr('\n\n')

                    cdr_model.finalize()
