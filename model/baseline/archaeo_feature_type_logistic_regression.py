"""
This script executes the task of estimating the number of inhabitants of a neighborhood to be under or over the
median of all neighborhoods, based solely on the geometry for that neighborhood. The data for this script can be
generated by running the prep/get-data.sh and prep/preprocess-neighborhoods.py scripts, which will take about
an hour or two.

The script itself will run for about two hours depending on your hardware, if you have at least a recent i7 or
comparable
"""

import multiprocessing
import os
import sys
from datetime import datetime, timedelta
from time import time

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.model_selection import cross_val_score, StratifiedShuffleSplit, GridSearchCV
from sklearn.preprocessing import StandardScaler

PACKAGE_PARENT = '..'
SCRIPT_DIR = os.path.dirname(os.path.realpath(os.path.join(os.getcwd(), os.path.expanduser(__file__))))
sys.path.append(os.path.normpath(os.path.join(SCRIPT_DIR, PACKAGE_PARENT)))

from topoml_util.slack_send import notify

SCRIPT_VERSION = '1.0.0'
SCRIPT_NAME = os.path.basename(__file__)
TIMESTAMP = str(datetime.now()).replace(':', '.')
NUM_CPUS = multiprocessing.cpu_count() - 1 or 1
DATA_FOLDER = SCRIPT_DIR + '/../../files/archaeology/'
FILENAME = 'archaeology_train_v4.npz'
EFD_ORDERS = [0, 1, 2, 3, 4, 6, 8, 12, 16, 20, 24]
SCRIPT_START = time()

if __name__ == '__main__':  # this is to squelch warnings on scikit-learn multithreaded grid search
    # Load training data
    train_loaded = np.load(DATA_FOLDER + FILENAME)
    train_fourier_descriptors = train_loaded['fourier_descriptors']
    train_labels = train_loaded['feature_type']

    scaler = StandardScaler().fit(train_fourier_descriptors)
    train_fourier_descriptors = scaler.transform(train_fourier_descriptors)

    # Grid search
    C_range = [1e-2, 1e-1, 1e0, 1e1, 1e2, 1e3]
    param_grid = dict(C=C_range)
    cv = StratifiedShuffleSplit(n_splits=5, test_size=0.2, random_state=42)
    grid = GridSearchCV(
        LogisticRegression(),
        n_jobs=NUM_CPUS,
        param_grid=param_grid,
        verbose=2,
        cv=cv)

    print('Performing grid search on model...')
    print('Using {} threads for grid search'.format(NUM_CPUS))
    print('Searching {} elliptic fourier descriptor orders'.format(EFD_ORDERS))

    best_order = 0
    best_score = 0
    best_params = {}

    for order in EFD_ORDERS:
        print('Fitting order {} fourier descriptors'.format(order))
        stop_position = 3 + (order * 8)
        grid.fit(train_fourier_descriptors[:, :stop_position], train_labels)
        print("The best parameters for order {} are {} with a score of {}\n".format(
            order, grid.best_params_, grid.best_score_))
        if grid.best_score_ > best_score:
            best_score = grid.best_score_
            best_order = order
            best_params = grid.best_params_

    print('Training model on order {} with best parameters {}'.format(
        best_order, best_params))
    stop_position = 3 + (best_order * 8)
    clf = LogisticRegression(C=best_params['C'])
    scores = cross_val_score(clf, train_fourier_descriptors[:, :stop_position], train_labels, cv=10, n_jobs=NUM_CPUS)
    print('Cross-validation scores:', scores)
    clf.fit(train_fourier_descriptors[:, :stop_position], train_labels)

    # Run predictions on unseen test data to verify generalization
    TEST_DATA_FILE = DATA_FOLDER + 'archaeology_test_v4.npz'
    test_loaded = np.load(TEST_DATA_FILE)
    test_fourier_descriptors = test_loaded['fourier_descriptors']
    test_labels = np.asarray(test_loaded['feature_type'], dtype=int)
    test_fourier_descriptors = scaler.transform(test_fourier_descriptors)

    print('Run on test data...')
    predictions = clf.predict(test_fourier_descriptors[:, :stop_position])
    test_accuracy = accuracy_score(test_labels, predictions)

    runtime = time() - SCRIPT_START
    message = '\nTest accuracy of {} for fourier descriptor order {} with {} in {}'.format(
        test_accuracy, best_order, best_params, timedelta(seconds=runtime))
    print(message)
    notify(SCRIPT_NAME, message)
