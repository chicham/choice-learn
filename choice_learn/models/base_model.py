"""Base class for choice models."""
import json
import os
import time
from abc import abstractmethod
from pathlib import Path

import numpy as np
import tensorflow as tf
import tqdm

import choice_learn.tf_ops as tf_ops


class ChoiceModel(object):
    """Base class for choice models."""

    def __init__(
        self,
        label_smoothing=0.0,
        normalize_non_buy=False,
        optimizer="Adam",
        tolerance=1e-8,
        callbacks=None,
        lr=0.001,
        epochs=1,
        batch_size=32,
    ):
        """Instantiates the ChoiceModel.

        Parameters
        ----------
        label_smoothing : float, optional
            Whether (then is ]O, 1[ value) or not (then can be None or 0) to use label smoothing,
        during training, by default 0.0
            by default None. Label smoothing is applied to LogLikelihood loss.
        normalize_non_buy : bool, optional
            Whether or not to add a normalization (then U=1) with the exit option in probabilites
            normalization,by default True
        callbacks : list of tf.kera callbacks, optional
            List of callbacks to add to model.fit, by default None and only add History
        optimizer : str, optional
            Name of the tf.keras.optimizers to be used, by default "Adam"
        tolerance : float, optional
            Tolerance for the L-BFGS optimizer if applied, by default 1e-8
        lr: float, optional
            Learning rate for the optimizer if applied, by default 0.001
        epochs: int, optional
            (Max) Number of epochs to train the model, by default 1
        batch_size: int, optional
        """
        self.is_fitted = False
        self.normalize_non_buy = normalize_non_buy
        self.label_smoothing = label_smoothing
        self.stop_training = False

        # Loss function wrapping tf.keras.losses.CategoricalCrossEntropy
        # with smoothing and normalization options
        self.loss = tf_ops.CustomCategoricalCrossEntropy(
            from_logits=False, label_smoothing=self.label_smoothing
        )
        self.callbacks = tf.keras.callbacks.CallbackList(callbacks, add_history=True, model=None)
        self.callbacks.set_model(self)

        # Was originally in BaseMNL, moved here.
        self.optimizer_name = optimizer
        if optimizer.lower() == "adam":
            self.optimizer = tf.keras.optimizers.Adam(lr)
        elif optimizer.lower() == "sgd":
            self.optimizer = tf.keras.optimizers.SGD(lr)
        elif optimizer.lower() == "adamax":
            self.optimizer = tf.keras.optimizers.Adamax(lr)
        elif optimizer.lower() == "lbfgs" or optimizer.lower() == "l-bfgs":
            print("Using L-BFGS optimizer, setting up .fit() function")
            self.fit = self._fit_with_lbfgs
        else:
            print(f"Optimizer {optimizer} not implemnted, switching for default Adam")
            self.optimizer = tf.keras.optimizers.Adam(lr)

        self.epochs = epochs
        self.batch_size = batch_size
        self.tolerance = tolerance

    @abstractmethod
    def compute_batch_utility(
        self,
        shared_features_by_choice,
        items_features_by_choice,
        available_items_by_choice,
        choices,
    ):
        """Method that defines how the model computes the utility of a product.

        MUST be implemented in children classe !
        For simpler use-cases this is the only method to be user-defined.

        Parameters
        ----------
        shared_features_by_choice : tuple of np.ndarray (choices_features)
            a batch of shared features
            Shape must be (n_choices, n_shared_features)
        items_features_by_choice : tuple of np.ndarray (choices_items_features)
            a batch of items features
            Shape must be (n_choices, n_items_features)
        available_items_by_choice : np.ndarray
            A batch of items availabilities
            Shape must be (n_choices, n_items)
        choices_batch : np.ndarray
            Choices
            Shape must be (n_choices, )

        Returns:
        --------
        np.ndarray
            Utility of each product for each choice.
            Shape must be (n_choices, n_items)
        """
        # To be implemented in children classes
        # Can be NumPy or TensorFlow based
        return

    @tf.function
    def train_step(
        self,
        shared_features_by_choice,
        items_features_by_choice,
        available_items_by_choice,
        choices,
        sample_weight=None,
    ):
        """Function that represents one training step (= one gradient descent step) of the model.

        Parameters
        ----------
        shared_features_by_choice : tuple of np.ndarray (choices_features)
            a batch of shared features
            Shape must be (n_choices, n_shared_features)
        items_features_by_choice : tuple of np.ndarray (choices_items_features)
            a batch of items features
            Shape must be (n_choices, n_items_features)
        available_items_by_choice : np.ndarray
            A batch of items availabilities
            Shape must be (n_choices, n_items)
        choices_batch : np.ndarray
            Choices
            Shape must be (n_choices, )
        sample_weight : np.ndarray, optional
            List samples weights to apply during the gradient descent to the batch elements,
            by default None

        Returns:
        --------
        tf.Tensor
            Value of NegativeLogLikelihood loss for the batch
        """
        with tf.GradientTape() as tape:
            utilities = self.compute_batch_utility(
                shared_features_by_choice=shared_features_by_choice,
                items_features_by_choice=items_features_by_choice,
                available_items_by_choice=available_items_by_choice,
                choices=choices,
            )

            probabilities = tf_ops.softmax_with_availabilities(
                items_logit_by_choice=utilities,
                available_items_by_choice=available_items_by_choice,
                normalize_exit=self.normalize_non_buy,
                axis=-1,
            )
            # Negative Log-Likelihood
            neg_loglikelihood = self.loss(
                y_pred=probabilities,
                y_true=tf.one_hot(choices, depth=probabilities.shape[1]),
                sample_weight=sample_weight,
            )

        grads = tape.gradient(neg_loglikelihood, self.weights)
        self.optimizer.apply_gradients(zip(grads, self.weights))
        return neg_loglikelihood

    def fit(
        self,
        choice_dataset,
        sample_weight=None,
        val_dataset=None,
        verbose=0,
        epochs=None,
        batch_size=None,
    ):
        """Method to train the model with a ChoiceDataset.

        Parameters
        ----------
        choice_dataset : ChoiceDataset
            Input data in the form of a ChoiceDataset
        sample_weight : np.ndarray, optional
            Sample weights to apply, by default None
        val_dataset : ChoiceDataset, optional
            Test ChoiceDataset to evaluate performances on test at each epoch, by default None
        verbose : int, optional
            print level, for debugging, by default 0
        epochs : int, optional
            Number of epochs, default is None, meaning we use self.epochs
        batch_size : int, optional
            Batch size, default is None, meaning we use self.batch_size

        Returns:
        --------
        dict:
            Different metrics values over epochs.
        """
        if hasattr(self, "instantiated"):
            if not self.instantiated:
                raise ValueError("Model not instantiated. Please call .instantiate() first.")
        if epochs is None:
            epochs = self.epochs
        if batch_size is None:
            batch_size = self.batch_size

        losses_history = {"train_loss": []}
        t_range = tqdm.trange(epochs, position=0)

        self.callbacks.on_train_begin()

        # Iterate of epochs
        for epoch_nb in t_range:
            self.callbacks.on_epoch_begin(epoch_nb)
            t_start = time.time()
            train_logs = {"train_loss": []}
            val_logs = {"val_loss": []}
            epoch_losses = []

            if sample_weight is not None:
                if verbose > 0:
                    inner_range = tqdm.tqdm(
                        choice_dataset.iter_batch(
                            shuffle=True, sample_weight=sample_weight, batch_size=batch_size
                        ),
                        total=int(len(choice_dataset) / np.max([1, batch_size])),
                        position=1,
                        leave=False,
                    )
                else:
                    inner_range = choice_dataset.iter_batch(
                        shuffle=True, sample_weight=sample_weight, batch_size=batch_size
                    )

                for batch_nb, (
                    (
                        shared_features_batch,
                        items_features_batch,
                        available_items_batch,
                        choices_batch,
                    ),
                    weight_batch,
                ) in enumerate(inner_range):
                    self.callbacks.on_train_batch_begin(batch_nb)

                    neg_loglikelihood = self.train_step(
                        shared_features_batch,
                        items_features_batch,
                        available_items_batch,
                        choices_batch,
                        sample_weight=weight_batch,
                    )

                    train_logs["train_loss"].append(neg_loglikelihood)
                    temps_logs = {k: tf.reduce_mean(v) for k, v in train_logs.items()}
                    self.callbacks.on_train_batch_end(batch_nb, logs=temps_logs)

                    # Optimization Steps
                    epoch_losses.append(neg_loglikelihood)

            # In this case we do not need to batch the sample_weights
            else:
                if verbose > 0:
                    inner_range = tqdm.tqdm(
                        choice_dataset.iter_batch(shuffle=True, batch_size=batch_size),
                        total=int(len(choice_dataset) / np.max([batch_size, 1])),
                        position=1,
                        leave=False,
                    )
                else:
                    inner_range = choice_dataset.iter_batch(shuffle=True, batch_size=batch_size)
                for batch_nb, (
                    shared_features_batch,
                    items_features_batch,
                    available_items_batch,
                    choices_batch,
                ) in enumerate(inner_range):
                    self.callbacks.on_train_batch_begin(batch_nb)
                    neg_loglikelihood = self.train_step(
                        shared_features_batch,
                        items_features_batch,
                        available_items_batch,
                        choices_batch,
                    )
                    train_logs["train_loss"].append(neg_loglikelihood)
                    temps_logs = {k: tf.reduce_mean(v) for k, v in train_logs.items()}
                    self.callbacks.on_train_batch_end(batch_nb, logs=temps_logs)

                    # Optimization Steps
                    epoch_losses.append(neg_loglikelihood)

            # Take into account last batch that may have a differnt length into account for
            # the computation of the epoch loss.
            if batch_size != -1:
                last_batch_size = available_items_batch.shape[0]
                coefficients = tf.concat(
                    [tf.ones(len(epoch_losses) - 1) * batch_size, [last_batch_size]], axis=0
                )
                epoch_lossses = tf.multiply(epoch_losses, coefficients)
                epoch_loss = tf.reduce_sum(epoch_lossses) / len(choice_dataset)
            else:
                epoch_loss = tf.reduce_mean(epoch_losses)
            losses_history["train_loss"].append(epoch_loss)
            desc = f"Epoch {epoch_nb} Train Loss {losses_history['train_loss'][-1].numpy()}"
            if verbose > 1:
                print(
                    f"Loop {epoch_nb} Time",
                    time.time() - t_start,
                    "Loss:",
                    tf.reduce_sum(epoch_losses).numpy(),
                )

            # Test on val_dataset if provided
            if val_dataset is not None:
                test_losses = []
                for batch_nb, (
                    shared_features_batch,
                    items_features_batch,
                    available_items_batch,
                    choices_batch,
                ) in enumerate(val_dataset.iter_batch(shuffle=False, batch_size=batch_size)):
                    self.callbacks.on_batch_begin(batch_nb)
                    self.callbacks.on_test_batch_begin(batch_nb)
                    test_losses.append(
                        self.batch_predict(
                            shared_features_batch,
                            items_features_batch,
                            available_items_batch,
                            choices_batch,
                        )[0]["optimized_loss"]
                    )
                    val_logs["val_loss"].append(test_losses[-1])
                    temps_logs = {k: tf.reduce_mean(v) for k, v in val_logs.items()}
                    self.callbacks.on_test_batch_end(batch_nb, logs=temps_logs)

                test_loss = tf.reduce_mean(test_losses)
                if verbose > 1:
                    print("Test Negative-LogLikelihood:", test_loss.numpy())
                    desc += f", Test Loss {test_loss.numpy()}"
                losses_history["test_loss"] = losses_history.get("test_loss", []) + [
                    test_loss.numpy()
                ]
                train_logs = {**train_logs, **val_logs}

            temps_logs = {k: tf.reduce_mean(v) for k, v in train_logs.items()}
            self.callbacks.on_epoch_end(epoch_nb, logs=temps_logs)
            if self.stop_training:
                print("Early Stopping taking effect")
                break
            if verbose > 0:
                t_range.set_description(desc)
                t_range.refresh()

        temps_logs = {k: tf.reduce_mean(v) for k, v in train_logs.items()}
        self.callbacks.on_train_end(logs=temps_logs)
        return losses_history

    @tf.function
    def batch_predict(
        self,
        shared_features_by_choice,
        items_features_by_choice,
        available_items_by_choice,
        choices,
        sample_weight=None,
    ):
        """Function that represents one prediction (Probas + Loss) for one batch of a ChoiceDataset.

        Parameters
        ----------
        shared_features_by_choice : tuple of np.ndarray (choices_features)
            a batch of shared features
            Shape must be (n_choices, n_shared_features)
        items_features_by_choice : tuple of np.ndarray (choices_items_features)
            a batch of items features
            Shape must be (n_choices, n_items_features)
        available_items_by_choice : np.ndarray
            A batch of items availabilities
            Shape must be (n_choices, n_items)
        choices_batch : np.ndarray
            Choices
            Shape must be (n_choices, )
        sample_weight : np.ndarray, optional
            List samples weights to apply during the gradient descent to the batch elements,
            by default None

        Returns:
        --------
        tf.Tensor (1, )
            Value of NegativeLogLikelihood loss for the batch
        tf.Tensor (batch_size, n_items)
            Probabilities for each product to be chosen for each choice
        """
        # Compute utilities from features
        utilities = self.compute_batch_utility(
            shared_features_by_choice,
            items_features_by_choice,
            available_items_by_choice,
            choices,
        )
        # Compute probabilities from utilities & availabilties
        probabilities = tf_ops.softmax_with_availabilities(
            items_logit_by_choice=utilities,
            available_items_by_choice=available_items_by_choice,
            normalize_exit=self.normalize_non_buy,
            axis=-1,
        )

        # Compute loss from probabilities & actual choices
        # batch_loss = self.loss(probabilities, c_batch, sample_weight=sample_weight)
        batch_loss = {
            "optimized_loss": self.loss(
                y_pred=probabilities,
                y_true=tf.one_hot(choices, depth=probabilities.shape[1]),
                sample_weight=sample_weight,
            ),
            "NegativeLogLikelihood": tf.keras.losses.CategoricalCrossentropy()(
                y_pred=probabilities,
                y_true=tf.one_hot(choices, depth=probabilities.shape[1]),
                sample_weight=sample_weight,
            ),
        }
        return batch_loss, probabilities

    def save_model(self, path):
        """Method to save the different models on disk.

        Parameters
        ----------
        path : str
            path to the folder where to save the model
        """
        if not os.exists(path):
            Path(path).mkdir(parents=True)

        for i, weight in enumerate(self.weights):
            tf.keras.savedmodel.save(Path(path) / f"weight_{i}")

        # To improve for non-string attributes
        params = self.__dict__
        json.dump(Path(path) / "params.json", params)

        # Save optimizer state

    @classmethod
    def load_model(cls, path):
        """Method to load a ChoiceModel previously saved with save_model().

        Parameters
        ----------
        path : str
            path to the folder where the saved model files are

        Returns:
        --------
        ChoiceModel
            Loaded ChoiceModel
        """
        obj = cls()
        obj.weights = []
        i = 0
        weight_path = f"weight_{i}"
        while weight_path in os.listdir(path):
            obj.weights.append(tf.keras.load_model.load(Path(path) / weight_path))
            i += 1
            weight_path = f"weight_{i}"

        # To improve for non string attributes
        params = json.load(Path(path) / "params.json")
        for k, v in params.items():
            setattr(obj, k, v)

        # Load optimizer step
        return cls

    def predict_probas(self, choice_dataset, batch_size=-1):
        """Predicts the choice probabilities for each choice and each product of a ChoiceDataset.

        Parameters
        ----------
        choice_dataset : ChoiceDataset
            Dataset on which to apply to prediction
        batch_size : int, optional
            Batch size to use for the prediction, by default -1

        Returns:
        --------
        np.ndarray (n_choices, n_items)
            Choice probabilties for each choice and each product
        """
        stacked_probabilities = []
        for (
            shared_features_by_choice,
            items_features_by_choice,
            available_items_by_choice,
            choices,
        ) in choice_dataset.iter_batch(batch_size=batch_size):
            _, probabilities = self.batch_predict(
                shared_features_by_choice=shared_features_by_choice,
                items_features_by_choice=items_features_by_choice,
                available_items_by_choice=available_items_by_choice,
                choices=choices,
            )
            stacked_probabilities.append(probabilities)

        return tf.concat(stacked_probabilities, axis=0)

    def evaluate(self, choice_dataset, sample_weight=None, batch_size=-1, mode="eval"):
        """Evaluates the model for each choice and each product of a ChoiceDataset.

        Predicts the probabilities according to the model and computes the Negative-Log-Likelihood
        loss from the actual choices.

        Parameters
        ----------
        choice_dataset : ChoiceDataset
            Dataset on which to apply to prediction

        Returns:
        --------
        np.ndarray (n_choices, n_items)
            Choice probabilties for each choice and each product
        """
        batch_losses = []
        for (
            shared_features_by_choice,
            items_features_by_choice,
            available_items_by_choice,
            choices,
        ) in choice_dataset.iter_batch(batch_size=batch_size):
            loss, _ = self.batch_predict(
                shared_features_by_choice=shared_features_by_choice,
                items_features_by_choice=items_features_by_choice,
                available_items_by_choice=available_items_by_choice,
                choices=choices,
                sample_weight=sample_weight,
            )
            if mode == "eval":
                batch_losses.append(loss["NegativeLogLikelihood"])
            elif mode == "optim":
                batch_losses.append(loss["optimized_loss"])
        if batch_size != -1:
            last_batch_size = available_items_by_choice.shape[0]
            coefficients = tf.concat(
                [tf.ones(len(batch_losses) - 1) * batch_size, [last_batch_size]], axis=0
            )
            batch_losses = tf.multiply(batch_losses, coefficients)
            batch_loss = tf.reduce_sum(batch_losses) / len(choice_dataset)
        else:
            batch_loss = tf.reduce_mean(batch_losses)
        return batch_loss

    def _lbfgs_train_step(self, dataset, sample_weight=None):
        """A factory to create a function required by tfp.optimizer.lbfgs_minimize.

        Parameters
        ----------
        dataset: ChoiceDataset
            Dataset on which to estimate the paramters.
        sample_weight: np.ndarray, optional
            Sample weights to apply, by default None

        Returns:
        --------
        function
            with the signature:
                loss_value, gradients = f(model_parameters).
        """
        # obtain the shapes of all trainable parameters in the model
        shapes = tf.shape_n(self.weights)
        n_tensors = len(shapes)

        # we'll use tf.dynamic_stitch and tf.dynamic_partition later, so we need to
        # prepare required information first
        count = 0
        idx = []  # stitch indices
        part = []  # partition indices

        for i, shape in enumerate(shapes):
            n = np.product(shape)
            idx.append(tf.reshape(tf.range(count, count + n, dtype=tf.int32), shape))
            part.extend([i] * n)
            count += n

        part = tf.constant(part)

        @tf.function
        def assign_new_model_parameters(params_1d):
            """A function updating the model's parameters with a 1D tf.Tensor.

            Pararmeters
            -----------
            params_1d: tf.Tensor
                a 1D tf.Tensor representing the model's trainable parameters.
            """
            params = tf.dynamic_partition(params_1d, part, n_tensors)
            for i, (shape, param) in enumerate(zip(shapes, params)):
                self.weights[i].assign(tf.reshape(param, shape))

        # now create a function that will be returned by this factory
        @tf.function
        def f(params_1d):
            """A function that can be used by tfp.optimizer.lbfgs_minimize.

            This function is created by function_factory.

            Parameters
            ----------
            params_1d: tf.Tensor
                a 1D tf.Tensor.

            Returns:
            --------
            tf.Tensor
                A scalar loss and the gradients w.r.t. the `params_1d`.
            tf.Tensor
                A 1D tf.Tensor representing the gradients w.r.t. the `params_1d`.
            """
            # use GradientTape so that we can calculate the gradient of loss w.r.t. parameters
            with tf.GradientTape() as tape:
                # update the parameters in the model
                assign_new_model_parameters(params_1d)
                # calculate the loss
                loss_value = self.evaluate(
                    dataset, sample_weight=sample_weight, batch_size=-1, mode="optim"
                )

            # calculate gradients and convert to 1D tf.Tensor
            grads = tape.gradient(loss_value, self.weights)
            grads = tf.dynamic_stitch(idx, grads)

            # print out iteration & loss
            f.iter.assign_add(1)

            # store loss value so we can retrieve later
            tf.py_function(f.history.append, inp=[loss_value], Tout=[])

            return loss_value, grads

        # store these information as members so we can use them outside the scope
        f.iter = tf.Variable(0)
        f.idx = idx
        f.part = part
        f.shapes = shapes
        f.assign_new_model_parameters = assign_new_model_parameters
        f.history = []
        return f

    def _fit_with_lbfgs(self, dataset, epochs=None, sample_weight=None, verbose=0):
        """Fit function for L-BFGS optimizer.

        Replaces the .fit method when the optimizer is set to L-BFGS.

        Parameters
        ----------
        dataset : ChoiceDataset
            Dataset to be used for coefficients estimations
        epochs : int
            Maximum number of epochs allowed to reach minimum
        sample_weight : np.ndarray, optional
            Sample weights to apply, by default None
        verbose : int, optional
            print level, for debugging, by default 0

        Returns:
        --------
        dict
            Fit history
        """
        # Only import tensorflow_probability if LBFGS optimizer is used, avoid unnecessary
        # dependency
        import tensorflow_probability as tfp

        if epochs is None:
            epochs = self.epochs
        func = self._lbfgs_train_step(dataset, sample_weight=sample_weight)

        # convert initial model parameters to a 1D tf.Tensor
        init_params = tf.dynamic_stitch(func.idx, self.weights)

        # train the model with L-BFGS solver
        results = tfp.optimizer.lbfgs_minimize(
            value_and_gradients_function=func,
            initial_position=init_params,
            max_iterations=epochs,
            tolerance=self.tolerance,
            f_absolute_tolerance=-1,
            f_relative_tolerance=-1,
        )

        # after training, the final optimized parameters are still in results.position
        # so we have to manually put them back to the model
        func.assign_new_model_parameters(results.position)
        if verbose > 0:
            print("L-BFGS Opimization finished:")
            print("---------------------------------------------------------------")
            print("Number of iterations:", results[2].numpy())
            print("Algorithm converged before reaching max iterations:", results[0].numpy())
        return func.history


class BaseLatentClassModel(object):  # TODO: should inherit ChoiceModel ?
    """Base Class to work with Mixtures of models."""

    def __init__(
        self,
        n_latent_classes,
        model_class,
        model_parameters,
        fit_method,
        epochs,
        optimizer=None,
        add_exit_choice=False,
        tolerance=1e-6,
        lr=0.001,
    ):
        """Instantiation of the model mixture.

        Parameters
        ----------
        n_latent_classes : int
            Number of latent classes
        model_class : BaseModel
            class of models to get a mixture of
        model_parameters : dict
            hyper-parameters of the models
        fit_method : str
            Method to estimate the parameters: "EM", "MLE".
        epochs : int
            Number of epochs to train the model.
        optimizer: str, optional
            Name of the tf.keras.optimizers to be used if one is used, by default None
        add_exit_choice : bool, optional
            Whether or not to add an exit choice, by default False
        tolerance: float, optional
            Tolerance for the L-BFGS optimizer if applied, by default 1e-6
        lr: float, optional
            Learning rate for the optimizer if applied, by default 0.001
        """
        self.n_latent_classes = n_latent_classes
        if isinstance(model_parameters, list):
            if not len(model_parameters) == n_latent_classes:
                raise ValueError(
                    """If you specify a list of hyper-parameters, it means that you want to use\
                    different hyper-parameters for each latent class. In this case, the length\
                        of the list must be equal to the number of latent classes."""
                )
            self.model_parameters = model_parameters
        else:
            self.model_parameters = [model_parameters] * n_latent_classes
        self.model_class = model_class
        self.fit_method = fit_method

        self.epochs = epochs
        self.add_exit_choice = add_exit_choice
        self.tolerance = tolerance
        self.optimizer = optimizer
        self.lr = lr

        self.loss = tf_ops.CustomCategoricalCrossEntropy(from_logits=False, label_smoothing=0)
        self.instantiated = False

    def instantiate(self, **kwargs):
        """Instantiation."""
        init_logit = tf.Variable(
            tf.random_normal_initializer(0.0, 0.02, seed=42)(shape=(self.n_latent_classes - 1,)),
            name="Latent-Logits",
        )
        self.latent_logits = init_logit
        self.models = [self.model_class(**mp) for mp in self.model_parameters]
        for model in self.models:
            model.instantiate(**kwargs)

    # @tf.function
    def batch_predict(
        self,
        shared_features_by_choice,
        items_features_by_choice,
        available_items_by_choice,
        choices,
        sample_weight=None,
    ):
        """Function that represents one prediction (Probas + Loss) for one batch of a ChoiceDataset.

        Parameters
        ----------
        shared_features_by_choice : tuple of np.ndarray (choices_features)
            a batch of shared features
            Shape must be (n_choices, n_shared_features)
        items_features_by_choice : tuple of np.ndarray (choices_items_features)
            a batch of items features
            Shape must be (n_choices, n_items_features)
        available_items_by_choice : np.ndarray
            A batch of items availabilities
            Shape must be (n_choices, n_items)
        choices: np.ndarray
            Choices
            Shape must be (n_choices, )
        sample_weight : np.ndarray, optional
            List samples weights to apply during the gradient descent to the batch elements,
            by default None

        Returns:
        --------
        tf.Tensor (1, )
            Value of NegativeLogLikelihood loss for the batch
        tf.Tensor (batch_size, n_items)
            Probabilities for each product to be chosen for each choice
        """
        # Compute utilities from features
        utilities = self.compute_batch_utility(
            shared_features_by_choice,
            items_features_by_choice,
            available_items_by_choice,
            choices,
        )

        latent_probabilities = tf.concat(
            [[tf.constant(1.0)], tf.math.exp(self.latent_logits)], axis=0
        )
        latent_probabilities = latent_probabilities / tf.reduce_sum(latent_probabilities)
        # Compute probabilities from utilities & availabilties
        probabilities = []
        for i, class_utilities in enumerate(utilities):
            class_probabilities = tf_ops.softmax_with_availabilities(
                items_logit_by_choice=utilities,
                available_items_by_choice=available_items_by_choice,
                normalize_exit=self.normalize_non_buy,
                axis=-1,
            )
            probabilities.append(class_probabilities * latent_probabilities[i])
        # Summing over the latent classes
        probabilities = tf.reduce_sum(probabilities, axis=0)

        # Compute loss from probabilities & actual choices
        # batch_loss = self.loss(probabilities, c_batch, sample_weight=sample_weight)
        batch_loss = {
            "optimized_loss": self.loss(
                y_pred=probabilities,
                y_true=tf.one_hot(choices, depth=probabilities.shape[1]),
                sample_weight=sample_weight,
            ),
            "NegativeLogLikelihood": tf.keras.losses.CategoricalCrossentropy()(
                y_pred=probabilities,
                y_true=tf.one_hot(choices, depth=probabilities.shape[1]),
                sample_weight=sample_weight,
            ),
        }
        return batch_loss, probabilities

    def compute_batch_utility(
        self,
        shared_features_by_choice,
        items_features_by_choice,
        available_items_by_choice,
        choices,
    ):
        """Latent class computation of utility.

        It computes the utility for each of the latent models and stores them in a list.

        Parameters
        ----------
        shared_features_by_choice : tuple of np.ndarray (choices_features)
            a batch of shared features
            Shape must be (n_choices, n_shared_features)
        items_features_by_choice : tuple of np.ndarray (choices_items_features)
            a batch of items features
            Shape must be (n_choices, n_items_features)
        available_items_by_choice : np.ndarray
            A batch of items availabilities
            Shape must be (n_choices, n_items)
        choices : np.ndarray
            Choices
            Shape must be (n_choices, )

        Returns:
        --------
        list of np.ndarray
            List of:
                Utility of each product for each choice.
                Shape must be (n_choices, n_items)
            for each of the latent models.
        """
        utilities = []
        # Iterates over latent models
        for model in self.models:
            model_utilities = model.compute_batch_utility(
                shared_features_by_choice=shared_features_by_choice,
                items_features_by_choice=items_features_by_choice,
                available_items_by_choice=available_items_by_choice,
                choices=choices,
            )
            utilities.append(model_utilities)
        return utilities

    def fit(self, dataset, sample_weight=None, verbose=0):
        """Fit the model on a ChoiceDataset.

        Parameters
        ----------
        dataset : ChoiceDataset
            Dataset to be used for coefficients estimations
        sample_weight : np.ndarray, optional
            sample weights to apply, by default None
        verbose : int, optional
            print level, for debugging, by default 0

        Returns:
        --------
        dict
            Fit history
        """
        if self.fit_method.lower() == "em":
            self.minf = np.log(1e-3)
            print("Expectation-Maximization estimation algorithm not well implemented yet.")
            return self._em_fit(dataset=dataset, sample_weight=sample_weight, verbose=verbose)

        if self.fit_method.lower() == "mle":
            if self.optimizer.lower() == "lbfgs" or self.optimizer.lower() == "l-bfgs":
                return self._fit_with_lbfgs(
                    dataset=dataset, sample_weight=sample_weight, verbose=verbose
                )

            return self._fit_normal(dataset=dataset, sample_weight=sample_weight, verbose=verbose)

        raise ValueError(f"Fit method not implemented: {self.fit_method}")

    def evaluate(self, choice_dataset, sample_weight=None, batch_size=-1, mode="eval"):
        """Evaluates the model for each choice and each product of a ChoiceDataset.

        Predicts the probabilities according to the model and computes the Negative-Log-Likelihood
        loss from the actual choices.

        Parameters
        ----------
        choice_dataset : ChoiceDataset
            Dataset on which to apply to prediction

        Returns:
        --------
        np.ndarray (n_choices, n_items)
            Choice probabilties for each choice and each product
        """
        batch_losses = []
        for (
            shared_features,
            items_features,
            available_items,
            choices,
        ) in choice_dataset.iter_batch(batch_size=batch_size):
            loss, _ = self.batch_predict(
                shared_features_by_choice==shared_features,
                items_features_by_choice=items_features,
                available_items_by_choice==available_items,
                choices=choices,
                sample_weight=sample_weight,
            )
            if mode == "eval":
                batch_losses.append(loss["NegativeLogLikelihood"])
            elif mode == "optim":
                batch_losses.append(loss["optimized_loss"])
        if batch_size != -1:
            last_batch_size = available_items.shape[0]
            coefficients = tf.concat(
                [tf.ones(len(batch_losses) - 1) * batch_size, [last_batch_size]], axis=0
            )
            batch_losses = tf.multiply(batch_losses, coefficients)
            batch_loss = tf.reduce_sum(batch_losses) / len(choice_dataset)
        else:
            batch_loss = tf.reduce_mean(batch_losses)
        return batch_loss

    def _lbfgs_train_step(self, dataset, sample_weight=None):
        """A factory to create a function required by tfp.optimizer.lbfgs_minimize.

        Parameters
        ----------
        dataset: ChoiceDataset
            Dataset on which to estimate the paramters.
        sample_weight: np.ndarray, optional
            Sample weights to apply, by default None

        Returns:
        --------
        function
            with the signature:
                loss_value, gradients = f(model_parameters).
        """
        # obtain the shapes of all trainable parameters in the model
        weights = []
        w_to_model = []
        w_to_model_indexes = []
        for i, model in enumerate(self.models):
            for j, w in enumerate(model.weights):
                weights.append(w)
                w_to_model.append(i)
                w_to_model_indexes.append(j)
        weights.append(self.latent_logits)
        w_to_model.append(-1)
        w_to_model_indexes.append(-1)
        shapes = tf.shape_n(weights)
        n_tensors = len(shapes)

        # we'll use tf.dynamic_stitch and tf.dynamic_partition later, so we need to
        # prepare required information first
        count = 0
        idx = []  # stitch indices
        part = []  # partition indices

        for i, shape in enumerate(shapes):
            n = np.product(shape)
            idx.append(tf.reshape(tf.range(count, count + n, dtype=tf.int32), shape))
            part.extend([i] * n)
            count += n

        part = tf.constant(part)

        @tf.function
        def assign_new_model_parameters(params_1d):
            """A function updating the model's parameters with a 1D tf.Tensor.

            Pararmeters
            -----------
            params_1d: tf.Tensor
                a 1D tf.Tensor representing the model's trainable parameters.
            """
            params = tf.dynamic_partition(params_1d, part, n_tensors)
            for i, (shape, param) in enumerate(zip(shapes, params)):
                if w_to_model[i] != -1:
                    self.models[w_to_model[i]].weights[w_to_model_indexes[i]].assign(
                        tf.reshape(param, shape)
                    )
                else:
                    self.latent_logits.assign(tf.reshape(param, shape))

        # now create a function that will be returned by this factory
        @tf.function
        def f(params_1d):
            """A function that can be used by tfp.optimizer.lbfgs_minimize.

            This function is created by function_factory.

            Parameters
            ----------
            params_1d: tf.Tensor
                a 1D tf.Tensor.

            Returns:
            --------
            tf.Tensor
                A scalar loss and the gradients w.r.t. the `params_1d`.
            tf.Tensor
                A 1D tf.Tensor representing the gradients w.r.t. the `params_1d`.
            """
            # use GradientTape so that we can calculate the gradient of loss w.r.t. parameters
            with tf.GradientTape() as tape:
                # update the parameters in the model
                assign_new_model_parameters(params_1d)
                # calculate the loss
                loss_value = self.evaluate(
                    dataset, sample_weight=sample_weight, batch_size=-1, mode="optim"
                )
            # calculate gradients and convert to 1D tf.Tensor
            grads = tape.gradient(loss_value, weights)
            grads = tf.dynamic_stitch(idx, grads)

            # print out iteration & loss
            f.iter.assign_add(1)

            # store loss value so we can retrieve later
            tf.py_function(f.history.append, inp=[loss_value], Tout=[])

            return loss_value, grads

        # store these information as members so we can use them outside the scope
        f.iter = tf.Variable(0)
        f.idx = idx
        f.part = part
        f.shapes = shapes
        f.assign_new_model_parameters = assign_new_model_parameters
        f.history = []
        return f

    def _fit_with_lbfgs(self, dataset, epochs=None, sample_weight=None, verbose=0):
        """Fit function for L-BFGS optimizer.

        Replaces the .fit method when the optimizer is set to L-BFGS.

        Parameters
        ----------
        dataset : ChoiceDataset
            Dataset to be used for coefficients estimations
        epochs : int
            Maximum number of epochs allowed to reach minimum
        sample_weight : np.ndarray, optional
            Sample weights to apply, by default None
        verbose : int, optional
            print level, for debugging, by default 0

        Returns:
        --------
        dict
            Fit history
        """
        # Only import tensorflow_probability if LBFGS optimizer is used, avoid unnecessary
        # dependency
        import tensorflow_probability as tfp

        if epochs is None:
            epochs = self.epochs
        func = self._lbfgs_train_step(dataset, sample_weight=sample_weight)

        # convert initial model parameters to a 1D tf.Tensor
        init = []
        for model in self.models:
            for w in model.weights:
                init.append(w)
        init.append(self.latent_logits)
        init_params = tf.dynamic_stitch(func.idx, init)

        # train the model with L-BFGS solver
        results = tfp.optimizer.lbfgs_minimize(
            value_and_gradients_function=func,
            initial_position=init_params,
            max_iterations=epochs,
            tolerance=-1,
            f_absolute_tolerance=self.tolerance,
            f_relative_tolerance=-1,
            x_tolerance=-1,
        )

        # after training, the final optimized parameters are still in results.position
        # so we have to manually put them back to the model
        func.assign_new_model_parameters(results.position)
        if verbose > 0:
            print("L-BFGS Opimization finished:")
            print("---------------------------------------------------------------")
            print("Number of iterations:", results[2].numpy())
            print("Algorithm converged before reaching max iterations:", results[0].numpy())
        return func.history

    def _gd_train_step(self, dataset, sample_weight=None):
        pass

    def _nothing(self, inputs):
        """_summary_.

        Parameters
        ----------
        inputs : _type_
            _description_

        Returns:
        --------
        _type_
            _description_
        """
        latent_probas = tf.clip_by_value(
            self.latent_logits - tf.reduce_max(self.latent_logits), self.minf, 0
        )
        latent_probas = tf.math.exp(latent_probas)
        # latent_probas = tf.math.abs(self.logit_latent_probas)  # alternative implementation
        latent_probas = latent_probas / tf.reduce_sum(latent_probas)
        proba_list = []
        avail = inputs[4]
        for q in range(self.n_latent_classes):
            combined = self.models[q].compute_batch_utility(*inputs)
            combined = tf.clip_by_value(
                combined - tf.reduce_max(combined, axis=1, keepdims=True), self.minf, 0
            )
            combined = tf.keras.layers.Activation(activation=tf.nn.softmax)(combined)
            # combined = tf.keras.layers.Softmax()(combined)
            combined = combined * avail
            combined = latent_probas[q] * tf.math.divide(
                combined, tf.reduce_sum(combined, axis=1, keepdims=True)
            )
            combined = tf.expand_dims(combined, -1)
            proba_list.append(combined)
            # print(combined.get_shape()) # it is useful to print the shape of tensors for debugging

        proba_final = tf.keras.layers.Concatenate(axis=2)(proba_list)
        return tf.math.reduce_sum(proba_final, axis=2, keepdims=False)

    def _expectation(self, dataset):
        predicted_probas = [model.predict_probas(dataset) for model in self.models]
        if np.sum(np.isnan(predicted_probas)) > 0:
            print("Nan in probas")
        predicted_probas = [
            latent
            * tf.gather_nd(
                params=proba,
                indices=tf.stack([tf.range(0, len(dataset), 1), dataset.choices], axis=1),
            )
            for latent, proba in zip(self.latent_logits, predicted_probas)
        ]

        # E-step
        ###### FILL THE CODE BELOW TO ESTIMATE THE WEIGHTS (weights = xxx)
        predicted_probas = np.stack(predicted_probas, axis=1) + 1e-10
        loss = np.sum(np.log(np.sum(predicted_probas, axis=1)))

        return predicted_probas / np.sum(predicted_probas, axis=1, keepdims=True), loss

    def _maximization(self, dataset, verbose=0):
        """_summary_.

        Parameters
        ----------
        dataset : _type_
            _description_
        verbose : int, optional
            print level, for debugging, by default 0

        Returns:
        --------
        _type_
            _description_
        """
        self.models = [self.model_class(**mp) for mp in self.model_parameters]
        # M-step: MNL estimation
        for q in range(self.n_latent_classes):
            self.models[q].fit(dataset, sample_weight=self.weights[:, q], verbose=verbose)

        # M-step: latent probability estimation
        latent_probas = np.sum(self.weights, axis=0)

        return latent_probas / np.sum(latent_probas)

    def _em_fit(self, dataset, verbose=0):
        """Fit with Expectation-Maximization Algorithm.

        Parameters
        ----------
        dataset: ChoiceDataset
            Dataset to be used for coefficients estimations
        verbose : int, optional
            print level, for debugging, by default 0

        Returns:
        --------
        list
            List of logits for each latent class
        list
            List of losses at each epoch
        """
        hist_logits = []
        hist_loss = []

        # Initialization
        for model in self.models:
            # model.instantiate()
            model.fit(dataset, sample_weight=np.random.rand(len(dataset)), verbose=verbose)
        for i in tqdm.trange(self.epochs):
            self.weights, loss = self._expectation(dataset)
            self.latent_logits = self._maximization(dataset, verbose=verbose)
            hist_logits.append(self.latent_logits)
            hist_loss.append(loss)
            if np.sum(np.isnan(self.latent_logits)) > 0:
                print("Nan in logits")
                break
        return hist_logits, hist_loss

    def predict_probas(self, choice_dataset, batch_size=-1):
        """Predicts the choice probabilities for each choice and each product of a ChoiceDataset.

        Parameters
        ----------
        choice_dataset : ChoiceDataset
            Dataset on which to apply to prediction
        batch_size : int, optional
            Batch size to use for the prediction, by default -1

        Returns:
        --------
        np.ndarray (n_choices, n_items)
            Choice probabilties for each choice and each product
        """
        stacked_probabilities = []
        for (
            shared_features,
            items_features,
            available_items,
            choices,
        ) in choice_dataset.iter_batch(batch_size=batch_size):
            _, probabilities = self.batch_predict(
                shared_features_by_choice=shared_features,
                items_features_by_choice=items_features,
                available_items_by_choice=available_items,
                choices=choices,
            )
            stacked_probabilities.append(probabilities)

        return tf.concat(stacked_probabilities, axis=0)
