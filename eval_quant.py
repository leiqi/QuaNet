import argparse
import pandas as pd
from sklearn.naive_bayes import MultinomialNB

from plot_correction import plot_corr
from quantification.baselines import EM_Quantifier, SVMperfQuantifier
from quantification.helpers import *
from util.helpers import *


# from inntt import *

def eval_metric(metric, prevs, *methods):
    return [metric(prevs, method_i) for method_i in methods]

def main(args):
    args = parseargs(args)
    args.plotdir = os.path.join(args.plotdir, args.data)

    (x_train, y_train), (x_val, y_val), (x_test, y_test) = loadDataset(dataset=args.data, vocabularysize=args.vocabularysize)
    print('x_train shape:', x_train.shape, 'prev', np.mean(y_train))
    print('x_val shape:', x_val.shape, 'prev', np.mean(y_val))
    print('x_test shape:', x_test.shape, 'prev', np.mean(y_test))

    print('loading classifier')
    class_net = torch.load(args.classmodel)
    class_net = class_net.cuda() if use_cuda else class_net

    print('loading quantifier')
    quant_net = torch.load(args.quantmodel)
    quant_net = quant_net.cuda() if use_cuda else quant_net

    model_conf = 'QN'+('-E' if args.use_embeddings else '') + \
                 ('-SL' if quant_net.stats_in_lin_layers else '') +\
                 ('-SS' if quant_net.stats_in_sequence else '')

    print('creating val_yhat and test_yhat')
    val_yhat = predict(class_net, x_val, args.use_embeddings)  # todo: read use_embeddings from quant_net
    test_yhat = predict(class_net, x_test, args.use_embeddings)  # todo: read use_embeddings from quant_net
    val_tpr = tpr(val_yhat, y_val)
    val_fpr = fpr(val_yhat, y_val)
    val_ptpr = ptpr(val_yhat, y_val)
    val_pfpr = pfpr(val_yhat, y_val)

    train_prev = np.mean(y_train)
    test_prev = np.mean(y_test)

    test_samples = (21 if args.include_bounds else 19)*100

    classes = 2 #todo: take from model
    input_size = classes if not args.use_embeddings else classes + 100 #todo: take from model

    test_yhat_pos, test_yhat_neg, test_pos_ids, test_neg_ids = split_pos_neg(test_yhat, y_test)

    prevs_range = define_prev_range(args.include_bounds)
    print(prevs_range)

    test_sample_yhat, test_sample_y, test_sample_prev, test_sample_stats, test_chosen = \
        quantification_uniform_sampling(test_pos_ids, test_neg_ids, test_yhat_pos, test_yhat_neg,
                                        val_tpr, val_fpr, val_ptpr, val_pfpr,
                                        input_size, test_samples, sample_size=args.samplelength, prevs_range=prevs_range,
                                        seed=args.seed)

    true_prevs = compute_true_prevalence(test_sample_prev)

    quant_net.eval()
    print('Computing net prevalences for {} samples of the test set'.format(test_samples))
    test_batch_phat = quant_batched_predictions(quant_net, test_sample_yhat, test_sample_stats,
                                                batchsize=test_samples // 10)

    # prevalence by sampling test -----------------------------------------------------------------------------
    net_prevs = []
    for i in range(test_samples):
        net_prevs.append(float(test_batch_phat[i, 0]))

    if not args.net_only:
        print('Computing classify & count based methods (cc, acc, pcc, apcc) for {} samples of the test set'.format(test_samples))
        cc_prevs, acc_prevs = compute_classify_count(test_sample_yhat, val_tpr, val_fpr, probabilistic=False)
        pcc_prevs, apcc_prevs = compute_classify_count(test_sample_yhat, val_ptpr, val_pfpr, probabilistic=True)

        print('Computing E-M, SVM_NKLD and SVM_Q methods')
        data_matrix = loadDataset(dataset=args.data, mode='matrix')

        optim_params = {'alpha': [1e-4, 1e-3, 1e-2, 1e-1, 1e0, 1e1, 1e2, 1e3, 1e4, 1e5], 'fit_prior': [True, False]}
        EMq = EM_Quantifier(probabilistic_learner=MultinomialNB, alpha=1)
        EM_prevs = compute_baseline(EMq, data_matrix, test_chosen, prevs_range, optim_params)

        optim_params={'C':[1e-2, 1e-1, 1e0, 1e1, 1e2, 1e3, 1e4]}
        SVMkld = SVMperfQuantifier(svmperf_base=SVMPERF_BASE, loss='kld', verbose=False)
        svm_kld_prevs = compute_baseline(SVMkld, data_matrix, test_chosen, prevs_range, optim_params)

        SVMnkld=SVMperfQuantifier(svmperf_base=SVMPERF_BASE, loss='nkld', verbose=False)
        svm_nkld_prevs = compute_baseline(SVMnkld, data_matrix, test_chosen, prevs_range, optim_params)

        SVMq = SVMperfQuantifier(svmperf_base=SVMPERF_BASE, loss='q', verbose=False)
        svm_q_prevs    = compute_baseline(SVMq, data_matrix, test_chosen, prevs_range, optim_params)


        mehotds_names = ['cc', 'pcc', 'acc', 'apcc', 'em', 'svm-nkld', 'svm-q', model_conf]
        methods_prevalences = [cc_prevs, pcc_prevs, acc_prevs, apcc_prevs, EM_prevs, svm_nkld_prevs, svm_q_prevs, net_prevs]

    else:
        mehotds_names = [model_conf]
        methods_prevalences = [net_prevs]

    print('Compute MAE, MSE, MNKLD, and MRAE')
    mae_samples = eval_metric(mae, true_prevs, *methods_prevalences)
    mse_samples = eval_metric(mse, true_prevs, *methods_prevalences)
    mnkld_samples = eval_metric(mnkld, true_prevs, *methods_prevalences)
    mkld_samples = eval_metric(mkld, true_prevs, *methods_prevalences)
    mrae_samples = eval_metric(mrae, true_prevs, *methods_prevalences)

    if not args.net_only:
        print('Samples MAE:\tcc={:.5f} pcc={:.5f} acc={:.5f} apcc={:.5f} em={:.5f} svm-nkld={:.5f} svm-q={:.5f} net={:.5f}'.format(*mae_samples))
        print('Samples MSE:\tcc={:.5f} pcc={:.5f} acc={:.5f} apcc={:.5f} em={:.5f} svm-nkld={:.5f} svm-q={:.5f} net={:.5f}'.format(*mse_samples))
        print('Samples MNKLD:\tcc={:.5f} pcc={:.5f} acc={:.5f} apcc={:.5f} em={:.5f} svm-nkld={:.5f} svm-q={:.5f} net={:.5f}'.format(*mnkld_samples))
        print('Samples MRAE:\tcc={:.5f} pcc={:.5f} acc={:.5f} apcc={:.5f} em={:.5f} svm-nkld={:.5f} svm-q={:.5f} net={:.5f}'.format(*mrae_samples))

    # plots ---------------------------------------------------------------------------------------------------
    methods_prevalences = np.array(methods_prevalences)
    title = args.data.upper() if args.data != 'kindle' else args.data.title()
    plot_corr(true_prevs, methods_prevalences, mehotds_names, savedir=args.plotdir, savename='corr'+model_conf+args.result_note+'.pdf',
              train_prev=train_prev, test_prev=None, title=title) #test_prev)
    # plot_bins(prevs, methods_prevalences, labels, mae, bins=10,
    #           savedir=args.plotdir, savename='bins_' + mae.__name__ +model_conf+ args.result_note+'.png')
    # plot_bins(prevs, methods_prevalences, labels, mse, bins=10,
    #           savedir=args.plotdir, savename='bins_' + mse.__name__ +model_conf+ args.result_note+'.png')


    # load or create the dataframe for the results file
    if os.path.exists(args.results):
        df = pd.read_csv(args.results, index_col=0)
    else:
        df = pd.DataFrame(columns=['method', 'metric', 'score', 'mode', 'dataset', 'notes'])

    # fill in the scores for each method and metric
    for metric_name, scores in zip(['mae','mse','mnkld','mkld','mrae'],[mae_samples,mse_samples,mnkld_samples,mkld_samples,mrae_samples]):
        for method_position, method_name in enumerate(mehotds_names):
            df.loc[len(df)] = [method_name, metric_name, scores[method_position], 'sample', args.data, args.result_note]

    create_if_not_exists(os.path.dirname(args.results))
    df.to_csv(args.results)



def parseargs(args):
    parser = argparse.ArgumentParser(description='Learn Quantifier Correction',
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument('data',
                        help='Name of the dataset. Valid ones are: imdb, hp, kindle')
    parser.add_argument('classmodel',
                        help='Path to the classifier model')
    parser.add_argument('quantmodel',
                        help='Path to the quantifier model')
    parser.add_argument('seed',
                        help='random seed to replicate the evaluation samples', type=int)
    parser.add_argument('-v', '--vocabularysize',
                        help='Maximum length of the vocabulary', type=int, default=5000)
    parser.add_argument('-E', '--use-embeddings',
                        help='use the embeddings as input to the quantifier', default=False, action='store_true')
    parser.add_argument('-S', '--samplelength',
                        help='Length of the samples (in number of documents)', type=int, default=500)
    parser.add_argument('--plotdir',
                        help='Path to the plots', type=str, default='./results/plots')
    parser.add_argument('--results',
                        help='Path to the results', type=str, default='./results/results.txt')
    parser.add_argument('--result-note',
                        help='Adds a note to the results (e.g., "run0")', type=str, default='')
    parser.add_argument('--include-bounds',
                        help='Include the bounds 0 and 1 in the sampling. If otherwise, the sampling will be done from'
                             '0.05 to 0.95', default=False, action='store_true')
    parser.add_argument('--net-only',
                        help='Do only append results for the net (skip all baselines and dont plot)', default=False, action='store_true')

    return parser.parse_args(args)

if __name__ == '__main__':
    import sys
    main(sys.argv[1:])
