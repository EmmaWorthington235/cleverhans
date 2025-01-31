import numpy as np
import tensorflow as tf
import tensorflow_datasets as tfds
from absl import app, flags
from easydict import EasyDict
from tensorflow.keras import Model
from tensorflow.keras.layers import Dense, Dropout, Flatten, Conv2D

from cleverhans.tf2.attacks.projected_gradient_descent import projected_gradient_descent
from cleverhans.tf2.attacks.fast_gradient_method import fast_gradient_method
from cleverhans.tf2.attacks.basic_iterative_method import basic_iterative_method
from cleverhans.tf2.attacks.madry_et_al import madry_et_al
from cleverhans.tf2.attacks.momentum_iterative_method import momentum_iterative_method
from cleverhans.tf2.attacks.carlini_wagner_l2 import carlini_wagner_l2
from cleverhans.tf2.attacks.carlini_wagner_l2_quantum import carlini_wagner_l2_quantum

FLAGS = flags.FLAGS


class Net(Model):
    def __init__(self):
        super(Net, self).__init__()
        self.conv1 = Conv2D(64, 8, strides=(2, 2), activation="relu", padding="same")
        self.conv2 = Conv2D(128, 6, strides=(2, 2), activation="relu", padding="valid")
        self.conv3 = Conv2D(128, 5, strides=(1, 1), activation="relu", padding="valid")
        self.dropout = Dropout(0.25)
        self.flatten = Flatten()
        self.dense1 = Dense(128, activation="relu")
        self.dense2 = Dense(10)

    def call(self, x):
        x = self.conv1(x)
        x = self.conv2(x)
        x = self.conv3(x)
        x = self.dropout(x)
        x = self.flatten(x)
        x = self.dense1(x)
        return self.dense2(x)


def ld_mnist():
    """Load training and test data."""

    def convert_types(image, label):
        image = tf.cast(image, tf.float32)
        image /= 255
        return image, label

    dataset, info = tfds.load(
        "mnist", data_dir="gs://tfds-data/datasets", with_info=True, as_supervised=True
    )
    mnist_train, mnist_test = dataset["train"], dataset["test"]
    mnist_train = mnist_train.map(convert_types).shuffle(10000).batch(128)
    mnist_test = mnist_test.map(convert_types).batch(128)
    return EasyDict(train=mnist_train, test=mnist_test)


def main(_):
    # Load training and test data
    data = ld_mnist()
    model = Net()
    loss_object = tf.losses.SparseCategoricalCrossentropy(from_logits=True)
    optimizer = tf.optimizers.Adam(learning_rate=0.001)

    # Metrics to track the different accuracies.
    train_loss = tf.metrics.Mean(name="train_loss")
    test_acc_clean = tf.metrics.SparseCategoricalAccuracy()
    test_acc_fgsm = tf.metrics.SparseCategoricalAccuracy()
    test_acc_pgd = tf.metrics.SparseCategoricalAccuracy()
    test_acc_bim = tf.metrics.SparseCategoricalAccuracy()
    test_acc_mea = tf.metrics.SparseCategoricalAccuracy()
    test_acc_mim = tf.metrics.SparseCategoricalAccuracy()
    test_acc_cw = tf.metrics.SparseCategoricalAccuracy()
    test_acc_cw_quantum = tf.metrics.SparseCategoricalAccuracy()

    @tf.function
    def train_step(x, y):
        with tf.GradientTape() as tape:
            predictions = model(x)
            loss = loss_object(y, predictions)
        gradients = tape.gradient(loss, model.trainable_variables)
        optimizer.apply_gradients(zip(gradients, model.trainable_variables))
        train_loss(loss)

    # Train model with adversarial training
    for epoch in range(FLAGS.nb_epochs):
        # keras like display of progress
        progress_bar_train = tf.keras.utils.Progbar(60000)
        for (x, y) in data.train:
            if FLAGS.adv_train:
                # Replace clean example with adversarial example for adversarial training
                x = projected_gradient_descent(model, x, FLAGS.eps, 0.01, 40, np.inf)
            train_step(x, y)
            progress_bar_train.add(x.shape[0], values=[("loss", train_loss.result())])

    # Evaluate on clean and adversarial data
    progress_bar_test = tf.keras.utils.Progbar(10000)
    for x, y in data.test:
        y_pred = model(x)
        test_acc_clean(y, y_pred)

        x_fgm = fast_gradient_method(model, x, FLAGS.eps, np.inf)
        y_pred_fgm = model(x_fgm)
        test_acc_fgsm(y, y_pred_fgm)

        x_pgd = projected_gradient_descent(model, x, FLAGS.eps, 0.01, 40, np.inf)
        y_pred_pgd = model(x_pgd)
        test_acc_pgd(y, y_pred_pgd)

        x_bim = basic_iterative_method(model, x, FLAGS.eps, eps_iter=0.01, nb_iter=40, norm=2)
        y_pred_bim = model(x_bim)
        test_acc_bim(y, y_pred_bim)

        x_mea = madry_et_al(model, x, eps=0.01, eps_iter=0.01, nb_iter=40, norm=np.inf)
        y_pred_mea = model(x_mea)
        test_acc_mea(y, y_pred_mea)

        x_mim = momentum_iterative_method(model, x, eps=0.01, eps_iter=0.01, nb_iter=40, norm=np.inf)
        y_pred_mim = model(x_mim)
        test_acc_mim(y, y_pred_mim)

        x_cw = carlini_wagner_l2(model, x, targeted=False, max_iterations=50)
        y_pred_cw = model(x_cw)
        test_acc_cw(y, y_pred_cw)
        
        x_cw_quantum = carlini_wagner_l2_quantum(model, x, targeted=False, max_iterations=10)
        y_pred_cw_quantum = model(x_cw_quantum)
        test_acc_cw_quantum(y, y_pred_cw_quantum)

        progress_bar_test.add(x.shape[0])

    print(
        "test acc on clean examples (%): {:.3f}".format(test_acc_clean.result() * 100)
    )
    print(
        "test acc on FGM adversarial examples (%): {:.3f}".format(
            test_acc_fgsm.result() * 100
        )
    )
    print(
        "test acc on PGD adversarial examples (%): {:.3f}".format(
            test_acc_pgd.result() * 100
        )
    )

    print(
        "test acc on BIM adversarial examples (%): {:.3f}".format(
            test_acc_bim.result() * 100
        )
    )

    print(
        "test acc on MEA adversarial examples (%): {:.3f}".format(
            test_acc_mea.result() * 100
        )
    )

    print(
        "test acc on MIM adversarial examples (%): {:.3f}".format(
            test_acc_mim.result() * 100
        )
    )

    print(
        "test acc on CW (original) adversarial examples (%): {:.3f}".format(
            test_acc_cw.result() * 100
        )
    )
    
    print(
        "test acc on CW (quantum) adversarial examples (%): {:.3f}".format(
            test_acc_cw_quantum.result() * 100
        )
    )


if __name__ == "__main__":
    flags.DEFINE_integer("nb_epochs", 8, "Number of epochs.")
    flags.DEFINE_float("eps", 0.3, "Total epsilon for FGM and PGD attacks.")
    flags.DEFINE_bool(
        "adv_train", False, "Use adversarial training (on PGD adversarial examples)."
    )
    app.run(main)
