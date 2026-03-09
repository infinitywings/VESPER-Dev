"""
VESPER Anomaly Detection Pipeline

ML models for context-aware IoT intrusion detection.

Models:
    - RandomForestDetector: Sklearn Random Forest baseline
    - XGBoostDetector: XGBoost gradient-boosted trees
    - CNN1DDetector: 1D Convolutional Neural Network
    - TransformerDetector: Temporal transformer for sequence classification

Training harness:
    - ModelTrainer: Unified training, evaluation, and experiment runner
"""

from vesper.evaluation.anomaly_detection.models import (
    RandomForestDetector,
    XGBoostDetector,
    CNN1DDetector,
    TransformerDetector,
)
from vesper.evaluation.anomaly_detection.trainer import ModelTrainer
