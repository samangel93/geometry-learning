"""
This script executes the task of estimating the building type, based solely on the geometry signature for that
building. The data for this script is committed in the repository and can be regenerated by running the
prep/get-data.sh and prep/preprocess-buildings.py scripts, which will take about an hour or two.

This script itself will run for about two hours depending on your hardware, if you have at least a recent i7 or
comparable
"""

import multiprocessing
import os
import sys
from datetime import datetime

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedShuffleSplit, GridSearchCV, cross_val_score
from sklearn.preprocessing import StandardScaler

PACKAGE_PARENT = '..'
SCRIPT_DIR = os.path.dirname(os.path.realpath(os.path.join(os.getcwd(), os.path.expanduser(__file__))))
sys.path.append(os.path.normpath(os.path.join(SCRIPT_DIR, PACKAGE_PARENT)))

from topoml_util.slack_send import notify

SCRIPT_VERSION = '0.0.1'
SCRIPT_NAME = os.path.basename(__file__)
TIMESTAMP = str(datetime.now()).replace(':', '.')
DATA_FOLDER = SCRIPT_DIR + '/../../files/buildings/'
FILENAME_PREFIX = 'buildings-train'
NUM_CPUS = multiprocessing.cpu_count() - 1 or 1

if __name__ == '__main__':  # this is to squelch warnings on scikit-learn multithreaded grid search
    # Load training data
    training_files = []
    for file in os.listdir(DATA_FOLDER):
        if file.startswith(FILENAME_PREFIX) and file.endswith('.npz'):
            training_files.append(file)

    train_fourier_descriptors = np.array([])
    train_building_type = np.array([])

    for index, file in enumerate(training_files):  # load and concatenate the training files
        train_loaded = np.load(DATA_FOLDER + file)

        if index == 0:
            train_fourier_descriptors = train_loaded['fourier_descriptors']
            train_building_type = train_loaded['building_type']
        else:
            train_fourier_descriptors = \
                np.append(train_fourier_descriptors, train_loaded['fourier_descriptors'], axis=0)
            train_building_type = \
                np.append(train_building_type, train_loaded['building_type'], axis=0)

    scaler = StandardScaler().fit(train_fourier_descriptors)
    train_fourier_descriptors = scaler.transform(train_fourier_descriptors)

    # Grid search
    C_range = [1e-3, 1e-2, 1e-1, 1e0, 1e1]
    param_grid = dict(C=C_range)
    cv = StratifiedShuffleSplit(n_splits=5, test_size=0.2, random_state=42)
    grid = GridSearchCV(
        LogisticRegression(verbose=True),
        n_jobs=NUM_CPUS,
        param_grid=param_grid, cv=cv)
    grid.fit(train_fourier_descriptors, train_building_type)

    print("The best parameters are %s with a score of %0.3f"
          % (grid.best_params_, grid.best_score_))

    clf = LogisticRegression(C=grid.best_params_['C'])

    print('Fitting data to model with best paramaters from grid search...')
    scores = cross_val_score(clf, train_fourier_descriptors, train_building_type, cv=10, n_jobs=NUM_CPUS)
    print('Cross-validation scores:', scores)
    clf.fit(train_fourier_descriptors, train_building_type)

    # Run predictions on unseen test data to verify generalization
    print('Run on test data...')
    TEST_DATA_FILE = DATA_FOLDER + 'buildings-test.npz'
    test_loaded = np.load(TEST_DATA_FILE)
    test_fourier_descriptors = test_loaded['fourier_descriptors']
    test_building_type = np.asarray(test_loaded['building_type'], dtype=int)
    test_fourier_descriptors = scaler.transform(test_fourier_descriptors)

    predictions = clf.predict(test_fourier_descriptors)

    correct = 0
    for prediction, expected in zip(predictions, test_building_type):
        if prediction == expected:
            correct += 1

    accuracy = correct / len(predictions)
    print('Test accuracy: %0.3f' % accuracy)

    message = 'test accuracy of {0} with C: {1} '.format(str(accuracy), grid.best_params_['C'])
    notify(SCRIPT_NAME, message)
    print(SCRIPT_NAME, 'finished successfully')
