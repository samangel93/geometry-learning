import os
import socket
import sys
from datetime import datetime, timedelta
from time import time

import numpy as np
from keras import Input
from keras.callbacks import TensorBoard
from keras.engine import Model
from keras.layers import Dense, Conv1D, MaxPooling1D, GlobalAveragePooling1D, Dropout
from keras.optimizers import Adam
from keras.preprocessing.sequence import pad_sequences
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split

from prep.ProgressBar import ProgressBar
from topoml_util import geom_scaler
from topoml_util.slack_send import notify

SCRIPT_VERSION = '2.0.0'
SCRIPT_NAME = os.path.basename(__file__)
TIMESTAMP = str(datetime.now()).replace(':', '.')
SIGNATURE = SCRIPT_NAME + ' ' + SCRIPT_VERSION + ' ' + TIMESTAMP
DATA_FOLDER = '../files/archaeology/'
FILENAME = 'archaeology_train_v4.npz'
SCRIPT_START = time()

# Hyperparameters
hp = {
    'BATCH_SIZE': int(os.getenv('BATCH_SIZE', 512)),
    'TRAIN_VALIDATE_SPLIT': float(os.getenv('TRAIN_VALIDATE_SPLIT', 0.1)),
    'REPEAT_DEEP_ARCH': int(os.getenv('REPEAT_DEEP_ARCH', 0)),
    'DENSE_SIZE': int(os.getenv('DENSE_SIZE', 32)),
    'EPOCHS': int(os.getenv('EPOCHS', 200)),
    'LEARNING_RATE': float(os.getenv('LEARNING_RATE', 1e-3)),
    'DROPOUT': float(os.getenv('DROPOUT', 0.0)),
    'GEOM_SCALE': float(os.getenv("GEOM_SCALE", 0)),  # If no default or 0: overridden when data is known
}
OPTIMIZER = Adam(lr=hp['LEARNING_RATE'])

# Load training data
train_loaded = np.load(DATA_FOLDER + FILENAME)
train_geoms = train_loaded['geoms']
train_labels = train_loaded['feature_type']

# Determine final test mode or standard
if len(sys.argv) > 1 and sys.argv[1] in ['-t', '--test']:
    print('Training in final test mode')
    TEST_DATA_FILE = '../files/archaeology/archaeology_test_v4.npz'
    test_loaded = np.load(TEST_DATA_FILE)
    test_geoms = test_loaded['geoms']
    test_labels = test_loaded['feature_type']
else:
    print('Training in standard training mode')
    # Split the training data in random seen/unseen sets
    train_geoms, test_geoms, train_labels, test_labels = train_test_split(train_geoms, train_labels, test_size=0.1)

# Normalize
geom_scale = hp['GEOM_SCALE'] or geom_scaler.scale(train_geoms)
train_geoms = geom_scaler.transform(train_geoms, geom_scale)
test_geoms = geom_scaler.transform(test_geoms, geom_scale)  # re-use variance from training

# Sort data according to sequence length
train_input_sorted = {}
train_labels_sorted = {}
train_labels__max = np.array(train_labels).max()

for geom, label in zip(train_geoms, train_labels):
    # Map types to one-hot vectors
    one_hot_label = np.zeros((train_labels__max + 1))
    one_hot_label[label] = 1

    sequence_len = geom.shape[0]

    if sequence_len in train_input_sorted:
        train_input_sorted[sequence_len].append(geom)
        train_labels_sorted[sequence_len].append(one_hot_label)
    else:
        train_input_sorted[sequence_len] = [geom]
        train_labels_sorted[sequence_len] = [one_hot_label]

test_input_sorted = {}
test_labels_sorted = {}

for geom, label in zip(test_geoms, test_labels):
    sequence_len = geom.shape[0]

    if sequence_len in test_input_sorted:
        test_input_sorted[sequence_len].append(geom)
        test_labels_sorted[sequence_len].append(label)
    else:
        test_input_sorted[sequence_len] = [geom]
        test_labels_sorted[sequence_len] = [label]


# Map to numpy arrays
for sequence_len in train_input_sorted.keys():
    # short sequences need to be padded to pass through the net bottleneck
    if sequence_len < 32:
        train_input_sorted[sequence_len] = pad_sequences(
            train_input_sorted[sequence_len], maxlen=32, padding='post', truncating='post')

    train_input_sorted[sequence_len] = np.array([seq for seq in train_input_sorted[sequence_len]])
    train_labels_sorted[sequence_len] = np.array([label for label in train_labels_sorted[sequence_len]])

for sequence_len in test_input_sorted.keys():
    if sequence_len < 32:
        test_input_sorted[sequence_len] = pad_sequences(
            test_input_sorted[sequence_len], maxlen=32, padding='post', truncating='post')

    test_input_sorted[sequence_len] = np.array([seq for seq in test_input_sorted[sequence_len]])
    test_labels_sorted[sequence_len] = np.array([label for label in test_labels_sorted[sequence_len]])

# Shape determination
geom_vector_len = train_geoms[0].shape[1]
output_size = train_labels__max + 1

# Build model
inputs = Input(shape=(None, geom_vector_len))
model = Conv1D(32, (5,), activation='relu')(inputs)
# model = Conv1D(32, (5,), activation='relu')(model)
model = MaxPooling1D(3)(model)
model = Conv1D(64, (5,), activation='relu')(model)
model = GlobalAveragePooling1D()(model)
model = Dense(hp['DENSE_SIZE'], activation='relu')(model)
model = Dropout(hp['DROPOUT'])(model)
model = Dense(output_size, activation='softmax')(model)

model = Model(inputs=inputs, outputs=model)
model.compile(
    loss='categorical_crossentropy',
    metrics=['accuracy'],
    optimizer=OPTIMIZER),
model.summary()

# Callbacks
callbacks = [TensorBoard(log_dir='./tensorboard_log/' + SIGNATURE, write_graph=False)]

pgb = ProgressBar()
for epoch in range(hp['EPOCHS']):
    for sequence_len in sorted(train_input_sorted.keys()):
        message = 'Epoch {} of {}, sequence length {}'.format(epoch + 1, hp['EPOCHS'], sequence_len)
        pgb.update_progress(epoch/hp['EPOCHS'], message)

        inputs = train_input_sorted[sequence_len]
        labels = train_labels_sorted[sequence_len]

        model.fit(
            x=inputs,
            y=labels,
            verbose=0,
            batch_size=hp['BATCH_SIZE'],
            validation_split=hp['TRAIN_VALIDATE_SPLIT'],
            callbacks=callbacks)

# Run on unseen test data
print('Run on test data...')
test_labels = []
test_pred = []
for sequence_len in test_input_sorted.keys():
    test_geoms = test_input_sorted[sequence_len]

    for label in test_labels_sorted[sequence_len]:
        test_labels.append(label)
    for pred in model.predict(test_geoms):
        test_pred.append(np.argmax(pred))

accuracy = accuracy_score(test_labels, test_pred)

runtime = time() - SCRIPT_START
message = 'on {} completed with accuracy of \n{:f} \nin {} in {} epochs\n'.format(
    socket.gethostname(), accuracy, timedelta(seconds=runtime), hp['EPOCHS'])

for key, value in sorted(hp.items()):
    message += '{}: {}\t'.format(key, value)

notify(SIGNATURE, message)
print(SCRIPT_NAME, 'finished successfully with', message)
