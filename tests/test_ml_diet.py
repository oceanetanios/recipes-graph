"""Unit test for the metrics of scripts/ml_diet.py (no Neo4j needed)."""

import numpy as np

from scripts.ml_diet import metrics


def test_metrics_on_a_perfect_and_a_wrong_prediction() -> None:
    """Perfect predictions give 1.0 everywhere; the keys are the five reported metrics."""
    y_true = np.array([0, 0, 1, 1])

    perfect = metrics(y_true, y_true, np.array([0.1, 0.2, 0.8, 0.9]))
    wrong = metrics(y_true, 1 - y_true, np.array([0.9, 0.8, 0.2, 0.1]))

    assert perfect == {"accuracy": 1.0, "precision": 1.0, "recall": 1.0, "f1": 1.0, "roc_auc": 1.0}
    assert wrong["accuracy"] == 0.0 and wrong["roc_auc"] == 0.0
