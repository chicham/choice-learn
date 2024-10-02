"""Tests ResLogit."""

import numpy as np
import tensorflow as tf

from choice_learn.datasets import load_swissmetro

# from choice_learn.models import ResLogit, SimpleMNL
from choice_learn.models import ResLogit

dataset = load_swissmetro()
dataset = dataset[:10]  # Reduce the dataset size for faster testing
n_items = np.shape(dataset.items_features_by_choice)[2]
n_shared_features = np.shape(dataset.shared_features_by_choice)[2]
n_items_features = np.shape(dataset.items_features_by_choice)[3]


def test_reslogit_fit_with_sgd():
    """Tests that ResLogit can fit with SGD."""
    global dataset

    model = ResLogit(lr=1e-6, epochs=30, optimizer="SGD", batch_size=32)
    model.instantiate(n_items, n_shared_features, n_items_features)
    eval_before = model.evaluate(dataset)
    tf.config.run_functions_eagerly(True)  # To help with the coverage calculation
    model.fit(dataset)
    eval_after = model.evaluate(dataset)
    assert eval_after <= eval_before


def test_reslogit_fit_with_adam():
    """Tests that ResLogit can fit with Adam."""
    global dataset

    model = ResLogit(epochs=20, optimizer="Adam", batch_size=32)
    model.instantiate(n_items, n_shared_features, n_items_features)
    eval_before = model.evaluate(dataset)
    model.fit(dataset)
    eval_after = model.evaluate(dataset)
    assert eval_after <= eval_before


def test_reslogit_fit_with_adamax():
    """Tests that ResLogit can fit with Adamax."""
    global dataset

    model = ResLogit(epochs=20, optimizer="Adamax", batch_size=32)
    model.instantiate(n_items, n_shared_features, n_items_features)
    eval_before = model.evaluate(dataset)
    model.fit(dataset)
    eval_after = model.evaluate(dataset)
    assert eval_after <= eval_before


def test_reslogit_fit_with_optimizer_not_implemented():
    """Tests that ResLogit can switch for default Adam.

    When it encounters an optimizer that is not implemented.
    """
    global dataset

    model = ResLogit(epochs=20, optimizer="xyz_not_implemented", batch_size=32)
    model.instantiate(n_items, n_shared_features, n_items_features)
    eval_before = model.evaluate(dataset)
    model.fit(dataset)
    eval_after = model.evaluate(dataset)
    assert eval_after <= eval_before


def test_reslogit_fit_with_none_intercept():
    """Tests that ResLogit can fit with intercept=None."""
    global dataset

    model = ResLogit(intercept=None, lr=1e-6, epochs=20, optimizer="SGD", batch_size=32)

    indexes, weights = model.instantiate(
        n_items=n_items, n_shared_features=n_shared_features, n_items_features=n_items_features
    )
    assert "intercept" not in indexes

    model.instantiate(n_items, n_shared_features, n_items_features)
    eval_before = model.evaluate(dataset)
    model.fit(dataset)
    eval_after = model.evaluate(dataset)
    assert eval_after <= eval_before


def test_reslogit_fit_with_item_intercept():
    """Tests that ResLogit can fit with intercept="item"."""
    global dataset

    model = ResLogit(intercept="item", lr=1e-6, epochs=20, optimizer="SGD", batch_size=32)

    indexes, weights = model.instantiate(
        n_items=n_items, n_shared_features=n_shared_features, n_items_features=n_items_features
    )
    assert "intercept" in indexes

    eval_before = model.evaluate(dataset)
    model.fit(dataset)
    eval_after = model.evaluate(dataset)
    assert eval_after <= eval_before


def test_reslogit_fit_with_item_full_intercept():
    """Tests that ResLogit can fit with intercept="item-full"."""
    global dataset

    model = ResLogit(intercept="item-full", lr=1e-6, epochs=20, optimizer="SGD", batch_size=32)

    indexes, weights = model.instantiate(
        n_items=n_items, n_shared_features=n_shared_features, n_items_features=n_items_features
    )
    assert "intercept" in indexes

    eval_before = model.evaluate(dataset)
    model.fit(dataset)
    eval_after = model.evaluate(dataset)
    assert eval_after <= eval_before


def test_reslogit_fit_with_other_intercept():
    """Tests that ResLogit can fit with another intercept."""
    global dataset

    model = ResLogit(
        intercept="xyz_other_intercept", lr=1e-6, epochs=20, optimizer="SGD", batch_size=32
    )

    indexes, weights = model.instantiate(
        n_items=n_items, n_shared_features=n_shared_features, n_items_features=n_items_features
    )
    assert "intercept" in indexes

    model.instantiate(n_items, n_shared_features, n_items_features)
    eval_before = model.evaluate(dataset)
    model.fit(dataset)
    eval_after = model.evaluate(dataset)
    assert eval_after <= eval_before


# def test_reslogit_comparison_with_simple_mnl():
#     """Tests that ResLogit can fit better than SimpleMNL."""
#     full_dataset = load_swissmetro() # Use the full dataset to compare the models

#     reslogit = ResLogit(
#         intercept="item", lr=1e-6, n_layers=0, epochs=100, optimizer="SGD", batch_size=32
#     )
#     reslogit_indexes, reslogit_initial_weights = reslogit.instantiate(
#         n_items=n_items, n_shared_features=n_shared_features, n_items_features=n_items_features
#     )
#     reslogit.fit(full_dataset)
#     reslogit_final_weights = reslogit.trainable_weights
#     reslogit_score = reslogit.evaluate(full_dataset)

#     simple_mnl = SimpleMNL(intercept="item", lr=1e-6, epochs=100, optimizer="SGD", batch_size=32)
#     simple_mnl_indexes, simple_mnl_initial_weights = simple_mnl.instantiate(
#         n_items=n_items, n_shared_features=n_shared_features, n_items_features=n_items_features
#     )
#     simple_mnl.fit(full_dataset)
#     simple_mnl_final_weights = simple_mnl.trainable_weights
#     simple_mnl_score = simple_mnl.evaluate(full_dataset)

#     assert reslogit_indexes == simple_mnl_indexes
#     for i in range(len(reslogit_initial_weights)):
#         assert np.allclose(
#             simple_mnl_initial_weights[i].numpy(),
#             reslogit_initial_weights[i].numpy(),
#             rtol=0,
#             atol=0.01,
#         )
#     assert np.abs(simple_mnl_score - reslogit_score) < 0.05
#     for i in range(len(reslogit_final_weights)):
#         assert np.allclose(
#             simple_mnl_final_weights[i].numpy(),
#             reslogit_final_weights[i].numpy(),
#             rtol=0,
#             atol=0.01,
#         )


def test_reslogit_different_n_layers():
    """Tests that ResLogit can fit with different n_layers."""
    global dataset

    for n_layers in [0, 1, 4, 16]:
        model = ResLogit(n_layers=n_layers, lr=1e-6, epochs=20, optimizer="SGD", batch_size=32)
        # The model can fit
        model.instantiate(n_items, n_shared_features, n_items_features)
        eval_before = model.evaluate(dataset)
        model.fit(dataset)
        eval_after = model.evaluate(dataset)
        assert eval_after <= eval_before

        # The global shape of the residual weights corresponds to the number of layers
        assert len(model.resnet_model.trainable_variables) == n_layers

        if n_layers > 0:
            for layer_idx in range(n_layers):
                # Each residual layer has a (n_items, n_items) matrix of weights
                assert model.resnet_model.trainable_variables[layer_idx].shape == (n_items, n_items)


def test_reslogit_different_layers_width():
    """Tests that ResLogit can fit with different custom widths for its residual layers."""
    global dataset

    list_n_layers = [0, 1, 4, 16]
    list_res_layers_width = [[], [], [128, 256, n_items], [2, 4, 8, 16] * 3 + [32, 64, n_items]]

    for n_layers, res_layers_width in zip(list_n_layers, list_res_layers_width):
        model = ResLogit(
            n_layers=n_layers,
            res_layers_width=res_layers_width,
            lr=1e-4,
            epochs=20,
            optimizer="Adam",
            batch_size=-1,
        )
        # The model can fit
        model.instantiate(n_items, n_shared_features, n_items_features)
        eval_before = model.evaluate(dataset)
        model.fit(dataset)
        eval_after = model.evaluate(dataset)
        if not tf.math.is_nan(eval_after):
            assert eval_after <= eval_before

        # The global shape of the residual weights corresponds to the number of layers
        assert len(model.resnet_model.trainable_variables) == n_layers

        if n_layers > 0:
            # The first residual layer has a (n_items, n_items) matrix of weights
            assert model.resnet_model.trainable_variables[0].shape == (n_items, n_items)

            for layer_idx in range(1, n_layers):
                # For i > 1, the i-th residual layer has a
                # (res_layers_width[i-2], res_layers_width[i-1]) matrix of weights
                layer_width = res_layers_width[layer_idx - 1]
                prev_layer_width = res_layers_width[layer_idx - 2]
                assert model.resnet_model.trainable_variables[layer_idx].shape == (
                    prev_layer_width,
                    layer_width,
                )

    # Check if the ValueError are raised when the res_layers_width is not consistent
    model = ResLogit(
        n_layers=4,
        res_layers_width=[2, 4, 8, n_items],
        lr=1e-6,
        epochs=20,
        optimizer="SGD",
        batch_size=32,
    )
    try:
        model.fit(dataset)
        # ValueError: The length of the res_layers_width list should be equal to n_layers - 1
        assert False
    except ValueError:
        assert True

    model = ResLogit(
        n_layers=4,
        res_layers_width=[2, 4, 8, 16],
        lr=1e-6,
        epochs=20,
        optimizer="SGD",
        batch_size=32,
    )
    try:
        model.fit(dataset)
        # ValueError: The last element of the res_layers_width list should be equal to n_items
        assert False
    except ValueError:
        assert True


def test_reslogit_different_activation():
    """Tests that ResLogit can fit with different activation functions for its residual layers."""
    global dataset

    list_activation = ["linear", "relu", "-relu", "tanh", "sigmoid", "softplus"]

    for activation_str in list_activation:
        model = ResLogit(
            n_layers=2,
            activation=activation_str,
            lr=1e-6,
            epochs=20,
            optimizer="SGD",
            batch_size=32,
        )
        # The model can fit
        model.instantiate(n_items, n_shared_features, n_items_features)
        eval_before = model.evaluate(dataset)
        model.fit(dataset)
        eval_after = model.evaluate(dataset)
        assert eval_after <= eval_before

    # Check if the ValueError is raised when the activation is not implemented
    model = ResLogit(
        n_layers=2,
        activation="xyz_not_implemented",
        lr=1e-6,
        epochs=20,
        optimizer="SGD",
        batch_size=32,
    )
    try:
        model.fit(dataset)
        # ValueError: The activation function is not implemented
        assert False
    except ValueError:
        assert True


def test_that_endpoints_run():
    """Dummy test to check that the endpoints run.

    No verification of results.
    """
    global dataset

    model = ResLogit(epochs=20)
    model.fit(dataset)
    model.evaluate(dataset)
    model.predict_probas(dataset)
    assert True