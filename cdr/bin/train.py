import argparse
import os
import sys
import re
import pickle
import numpy as np
import pandas as pd

pd.options.mode.chained_assignment = None

from cdr.kwargs import CDR_INITIALIZATION_KWARGS, CDRMLE_INITIALIZATION_KWARGS, CDRBAYES_INITIALIZATION_KWARGS
from cdr.config import Config
from cdr.io import read_data
from cdr.formula import Formula
from cdr.data import add_dv, filter_invalid_responses, preprocess_data, compute_splitID, compute_partition
from cdr.util import mse, mae, filter_models, get_partition_list, paths_from_partition_cliarg, stderr


spillover = re.compile('(z_)?([^ (),]+)S([0-9]+)')


if __name__ == '__main__':

    argparser = argparse.ArgumentParser('''
        Trains model(s) from formula string(s) given data.
    ''')
    argparser.add_argument('config_path', help='Path to configuration (*.ini) file')
    argparser.add_argument('-m', '--models', nargs='*', default = [], help='Path to configuration (*.ini) file')
    argparser.add_argument('-p', '--partition', type=str, default='train', help='Name of partition to train on ("train", "dev", "test", or space- or hyphen-delimited subset of these)')
    argparser.add_argument('-e', '--force_training_evaluation', action='store_true', help='Recompute training evaluation even for models that are already finished.')
    argparser.add_argument('-s', '--save_and_exit', action='store_true', help='Initialize, save, and exit (CDR only). Useful for bringing non-backward compatible trained models up to spec for plotting and evaluation.')
    args, unknown = argparser.parse_known_args()

    p = Config(args.config_path)

    if not p.use_gpu_if_available:
        os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

    models = filter_models(p.model_list, args.models)

    run_baseline = False
    run_cdr = False
    for m in models:
        if not run_baseline and m.startswith('LM') or m.startswith('GAM'):
            run_baseline = True
        elif not run_cdr and (m.startswith('CDR') or m.startswith('DTSR')):
            run_cdr = True

    if not (run_baseline or run_cdr):
        stderr('No models to run. Exiting...\n')
        exit()

    cdr_formula_list = [Formula(p.models[m]['formula']) for m in models if (m.startswith('CDR') or m.startswith('DTSR'))]
    cdr_formula_name_list = [m for m in p.model_list if (m.startswith('CDR') or m.startswith('DTSR'))]
    all_rangf = [v for x in cdr_formula_list for v in x.rangf]
    partitions = get_partition_list(args.partition)
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

    if run_baseline:
        from cdr.baselines import py2ri
        assert len(X) == 1, 'Cannot run baselines on asynchronously sampled predictors'
        X_cur = X[0]
        X_cur['splitID'] = compute_splitID(X_cur, p.split_ids)
        part = compute_partition(X_cur, p.modulus, 3)
        part_select = None
        partition_name_to_ix = {'train': 0, 'dev': 1, 'test': 2}
        for partition in partitions:
            if part_select is None:
                part_select = part[partition_name_to_ix[partition]]
            else:
                part_select &= part[partition_name_to_ix[partition]]

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

        for c in y.columns:
            if c not in X_baseline.columns:
                X_baseline[c] = y[c] 

        for c in X_baseline.columns:
            if X_baseline[c].dtype.name == 'category':
                X_baseline[c] = X_baseline[c].astype(str)

        X_baseline.corr().to_csv('X_baseline_corr.csv', sep=' ', na_rep='NaN')
        X_baseline.to_csv('X_baseline.csv', sep=' ', index=False, na_rep=True)

        X_baseline = py2ri(X_baseline)

    n_train_sample = len(y)

    for m in models:
        p.set_model(m)
        formula = p['formula']
        if not os.path.exists(p.outdir + '/' + m):
            os.makedirs(p.outdir + '/' + m)
        if m.startswith('LME'):
            from cdr.baselines import LME

            dv = formula.strip().split('~')[0].strip().replace('.','')

            if os.path.exists(p.outdir + '/' + m + '/m.obj'):
                stderr('Retrieving saved model %s...\n' % m)
                with open(p.outdir + '/' + m + '/m.obj', 'rb') as m_file:
                    lme = pickle.load(m_file)
            else:
                stderr('Fitting model %s...\n' % m)
                lme = LME(formula, X_baseline)
                with open(p.outdir + '/' + m + '/m.obj', 'wb') as m_file:
                    pickle.dump(lme, m_file)

            lme_preds = lme.predict(X_baseline)
            lme_mse = mse(y[dv], lme_preds)
            lme_mae = mae(y[dv], lme_preds)
            summary = '=' * 50 + '\n'
            summary += 'LME regression\n\n'
            summary += 'Model name: %s\n\n' %m
            summary += 'Formula:\n'
            summary += '  ' + formula + '\n'
            summary += str(lme.summary()) + '\n'
            summary += 'Training set loss:\n'
            summary += '  MSE: %.4f\n' % lme_mse
            summary += '  MAE: %.4f\n' % lme_mae
            summary += '=' * 50 + '\n'
            with open(p.outdir + '/' + m + '/summary.txt', 'w') as f_out:
                f_out.write(summary)
            stderr(summary)
            stderr('\n\n')

        elif m.startswith('LM'):
            from cdr.baselines import LM

            dv = formula.strip().split('~')[0].strip().replace('.','')

            if os.path.exists(p.outdir + '/' + m + '/m.obj'):
                stderr('Retrieving saved model %s...\n' % m)
                with open(p.outdir + '/' + m + '/m.obj', 'rb') as m_file:
                    lm = pickle.load(m_file)
            else:
                stderr('Fitting model %s...\n' % m)
                lm = LM(formula, X_baseline)
                with open(p.outdir + '/' + m + '/m.obj', 'wb') as m_file:
                    pickle.dump(lm, m_file)

            lm_preds = lm.predict(X_baseline)
            lm_mse = mse(y[dv], lm_preds)
            lm_mae = mae(y[dv], lm_preds)
            summary = '=' * 50 + '\n'
            summary += 'Linear regression\n\n'
            summary += 'Model name: %s\n\n' %m
            summary += 'Formula:\n'
            summary += '  ' + formula + '\n'
            summary += str(lm.summary()) + '\n'
            summary += 'Training set loss:\n'
            summary += '  MSE: %.4f\n' % lm_mse
            summary += '  MAE: %.4f\n' % lm_mae
            summary += '=' * 50 + '\n'
            with open(p.outdir + '/' + m + '/summary.txt', 'w') as f_out:
                f_out.write(summary)
            stderr(summary)
            stderr('\n\n')

        elif m.startswith('GAM'):
            import re
            from cdr.baselines import GAM

            dv = formula.strip().split('~')[0].strip().replace('.','')
            ran_gf = ['subject', 'word', 'sentid']

            ## For some reason, GAM can't predict using custom functions, so we have to translate them
            z_term = re.compile('z.\((.*)\)')
            c_term = re.compile('c.\((.*)\)')
            formula = [t.strip() for t in formula.strip().split() if t.strip() != '']
            for i in range(len(formula)):
                formula[i] = z_term.sub(r'scale(\1)', formula[i])
                formula[i] = c_term.sub(r'scale(\1, scale=FALSE)', formula[i])
            formula = ' '.join(formula)

            if os.path.exists(p.outdir + '/' + m + '/m.obj'):
                stderr('Retrieving saved model %s...\n' % m)
                with open(p.outdir + '/' + m + '/m.obj', 'rb') as m_file:
                    gam = pickle.load(m_file)
            else:
                stderr('Fitting model %s...\n' % m)
                gam = GAM(formula, X_baseline, ran_gf=ran_gf)
                with open(p.outdir + '/' + m + '/m.obj', 'wb') as m_file:
                    pickle.dump(gam, m_file)

            gam_preds = gam.predict(X_baseline)
            gam_mse = mse(y[dv], gam_preds)
            gam_mae = mae(y[dv], gam_preds)
            summary = '=' * 50 + '\n'
            summary += 'GAM regression\n\n'
            summary += 'Model name: %s\n\n' %m
            summary += 'Formula:\n'
            summary += '  ' + formula + '\n'
            summary += str(gam.summary()) + '\n'
            summary += 'Training set loss:\n'
            summary += '  MSE: %.4f\n' % gam_mse
            summary += '  MAE: %.4f\n' % gam_mae
            summary += '=' * 50 + '\n'
            with open(p.outdir + '/' + m + '/summary.txt', 'w') as f_out:
                f_out.write(summary)
            stderr(summary)
            stderr('\n\n')

        elif m.startswith('CDR') or m.startswith('DTSR'):
            dv = formula.strip().split('~')[0].strip()
            y_valid, select_y_valid = filter_invalid_responses(y, dv)
            X_response_aligned_predictors_valid = X_response_aligned_predictors
            if X_response_aligned_predictors_valid is not None:
                X_response_aligned_predictors_valid = X_response_aligned_predictors_valid[select_y_valid]

            stderr('\nInitializing model %s...\n\n' % m)

            if p['network_type'] in ['mle', 'nn']:
                bayes = False
            else:
                bayes = True

            kwargs = {}
            for kwarg in CDR_INITIALIZATION_KWARGS:
                if kwarg.key not in ['outdir', 'history_length']:
                    kwargs[kwarg.key] = p[kwarg.key]

            if p['network_type'] in ['mle', 'nn']:
                from cdr.cdrmle import CDRMLE

                for kwarg in CDRMLE_INITIALIZATION_KWARGS:
                    kwargs[kwarg.key] = p[kwarg.key]

                cdr_model = CDRMLE(
                    formula,
                    X,
                    y_valid,
                    outdir=p.outdir + '/' + m,
                    history_length=p.history_length,
                    **kwargs
                )
            elif p['network_type'].startswith('bayes'):
                from cdr.cdrbayes import CDRBayes

                for kwarg in CDRBAYES_INITIALIZATION_KWARGS:
                    kwargs[kwarg.key] = p[kwarg.key]

                cdr_model = CDRBayes(
                    formula,
                    X,
                    y_valid,
                    outdir=p.outdir + '/' + m,
                    history_length=p.history_length,
                    **kwargs
                )
            else:
                raise ValueError('Network type "%s" not supported' %p['network_type'])

            if args.save_and_exit:
                cdr_model.save()
                continue

            stderr('\nFitting model %s...\n\n' % m)

            cdr_model.fit(
                X,
                y_valid,
                n_iter=p['n_iter'],
                X_response_aligned_predictor_names=X_response_aligned_predictor_names,
                X_response_aligned_predictors=X_response_aligned_predictors_valid,
                X_2d_predictor_names=X_2d_predictor_names,
                X_2d_predictors=X_2d_predictors,
                force_training_evaluation=args.force_training_evaluation,
                irf_name_map=p.irf_name_map,
                plot_n_time_units=p['plot_n_time_units'],
                plot_n_time_points=p['plot_n_time_points'],
                plot_x_inches=p['plot_x_inches'],
                plot_y_inches=p['plot_y_inches'],
                cmap=p['cmap'],
                dpi=p['dpi']
            )

            summary = cdr_model.summary()

            with open(p.outdir + '/' + m + '/summary.txt', 'w') as f_out:
                f_out.write(summary)
            stderr(summary)
            stderr('\n\n')

            cdr_model.save_parameter_table()

            cdr_model.finalize()

