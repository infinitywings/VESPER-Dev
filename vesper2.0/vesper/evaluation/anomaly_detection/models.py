"""
VESPER Anomaly Detection Models

Four model architectures for IoT intrusion detection:
1. RandomForestDetector  — sklearn Random Forest (binary + multi-class)
2. XGBoostDetector       — XGBoost gradient-boosted trees
3. CNN1DDetector         — 1D Convolutional Neural Network (PyTorch)
4. TransformerDetector   — Temporal Transformer encoder (PyTorch)

All models implement a common interface:
    .fit(X_train, y_train)
    .predict(X)  -> labels
    .predict_proba(X) -> probabilities
    .save(path) / .load(path)
"""

from __future__ import annotations

import json
import logging
import pickle
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

logger = logging.getLogger(__name__)


# ─── Base class ───────────────────────────────────────────────────────────────

class BaseDetector(ABC):
    """Common interface for all anomaly detection models."""

    name: str = "base"

    @abstractmethod
    def fit(self, X: np.ndarray, y: np.ndarray, **kwargs) -> Dict[str, Any]:
        """Train the model. Returns training metrics dict."""
        ...

    @abstractmethod
    def predict(self, X: np.ndarray) -> np.ndarray:
        """Predict labels for input features."""
        ...

    @abstractmethod
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Predict class probabilities."""
        ...

    @abstractmethod
    def save(self, path: str) -> None:
        """Save model to disk."""
        ...

    @abstractmethod
    def load(self, path: str) -> None:
        """Load model from disk."""
        ...

    def get_params(self) -> Dict[str, Any]:
        """Return model hyperparameters."""
        return {}


# ─── Random Forest ────────────────────────────────────────────────────────────

class RandomForestDetector(BaseDetector):
    """
    Random Forest classifier for anomaly detection.

    Good baseline with built-in feature importance.
    """

    name = "random_forest"

    def __init__(
        self,
        n_estimators: int = 200,
        max_depth: Optional[int] = None,
        min_samples_leaf: int = 2,
        class_weight: str = "balanced",
        random_state: int = 42,
        n_jobs: int = -1,
    ):
        self.params = {
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "min_samples_leaf": min_samples_leaf,
            "class_weight": class_weight,
            "random_state": random_state,
            "n_jobs": n_jobs,
        }
        self.model = None
        self._feature_importances = None

    def fit(self, X: np.ndarray, y: np.ndarray, **kwargs) -> Dict[str, Any]:
        from sklearn.ensemble import RandomForestClassifier

        self.model = RandomForestClassifier(**self.params)
        self.model.fit(X, y)
        self._feature_importances = self.model.feature_importances_

        return {
            "n_estimators": self.params["n_estimators"],
            "n_features": X.shape[1],
            "n_samples": X.shape[0],
            "n_classes": len(np.unique(y)),
            "oob_score": self.model.oob_score_ if self.model.oob_score else None,
        }

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.model.predict(X)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self.model.predict_proba(X)

    def feature_importances(self) -> np.ndarray:
        return self._feature_importances

    def save(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self.model, f)

    def load(self, path: str) -> None:
        with open(path, "rb") as f:
            self.model = pickle.load(f)
        self._feature_importances = self.model.feature_importances_

    def get_params(self) -> Dict[str, Any]:
        return self.params.copy()


# ─── XGBoost ─────────────────────────────────────────────────────────────────

class XGBoostDetector(BaseDetector):
    """
    XGBoost gradient-boosted tree classifier.

    Generally achieves best tabular performance.
    """

    name = "xgboost"

    def __init__(
        self,
        n_estimators: int = 300,
        max_depth: int = 6,
        learning_rate: float = 0.1,
        subsample: float = 0.8,
        colsample_bytree: float = 0.8,
        min_child_weight: int = 3,
        scale_pos_weight: float = 1.0,
        random_state: int = 42,
        n_jobs: int = -1,
    ):
        self.params = {
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "learning_rate": learning_rate,
            "subsample": subsample,
            "colsample_bytree": colsample_bytree,
            "min_child_weight": min_child_weight,
            "scale_pos_weight": scale_pos_weight,
            "random_state": random_state,
            "n_jobs": n_jobs,
        }
        self.model = None

    def fit(self, X: np.ndarray, y: np.ndarray, **kwargs) -> Dict[str, Any]:
        try:
            from xgboost import XGBClassifier
        except ImportError:
            raise ImportError("xgboost not installed. Install with: pip install xgboost")

        eval_set = kwargs.get("eval_set")
        early_stopping = kwargs.get("early_stopping_rounds", 20)

        xgb_params = {**self.params}
        # Auto-detect binary vs multi-class
        n_classes = len(np.unique(y))
        if n_classes == 2:
            xgb_params["objective"] = "binary:logistic"
            xgb_params["eval_metric"] = "logloss"
        else:
            xgb_params["objective"] = "multi:softprob"
            xgb_params["eval_metric"] = "mlogloss"
            xgb_params["num_class"] = n_classes

        self.model = XGBClassifier(**xgb_params)

        fit_kwargs = {}
        if eval_set:
            fit_kwargs["eval_set"] = eval_set
            fit_kwargs["verbose"] = False

        self.model.fit(X, y, **fit_kwargs)

        return {
            "n_estimators": self.params["n_estimators"],
            "best_iteration": getattr(self.model, "best_iteration", None),
            "n_features": X.shape[1],
            "n_samples": X.shape[0],
            "n_classes": n_classes,
        }

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.model.predict(X)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self.model.predict_proba(X)

    def feature_importances(self) -> np.ndarray:
        return self.model.feature_importances_

    def save(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.model.save_model(path)

    def load(self, path: str) -> None:
        from xgboost import XGBClassifier
        self.model = XGBClassifier()
        self.model.load_model(path)

    def get_params(self) -> Dict[str, Any]:
        return self.params.copy()


# ─── 1D CNN (PyTorch) ────────────────────────────────────────────────────────

class CNN1DDetector(BaseDetector):
    """
    1D Convolutional Neural Network for feature-vector classification.

    Architecture:
        Input → Conv1D(64) → BN → ReLU → Conv1D(128) → BN → ReLU →
        GlobalAvgPool → FC(64) → Dropout → FC(n_classes)
    """

    name = "cnn_1d"

    def __init__(
        self,
        n_epochs: int = 50,
        batch_size: int = 32,
        learning_rate: float = 1e-3,
        dropout: float = 0.3,
        hidden_dim: int = 64,
        random_state: int = 42,
    ):
        self.params = {
            "n_epochs": n_epochs,
            "batch_size": batch_size,
            "learning_rate": learning_rate,
            "dropout": dropout,
            "hidden_dim": hidden_dim,
            "random_state": random_state,
        }
        self.model = None
        self.n_classes = 2
        self.n_features = 0
        self._device = "cpu"

    def _build_model(self, n_features: int, n_classes: int):
        import torch
        import torch.nn as nn

        class CNN1D(nn.Module):
            def __init__(self, n_feat, n_cls, hidden, dropout):
                super().__init__()
                self.conv1 = nn.Conv1d(1, 64, kernel_size=3, padding=1)
                self.bn1 = nn.BatchNorm1d(64)
                self.conv2 = nn.Conv1d(64, 128, kernel_size=3, padding=1)
                self.bn2 = nn.BatchNorm1d(128)
                self.pool = nn.AdaptiveAvgPool1d(1)
                self.fc1 = nn.Linear(128, hidden)
                self.drop = nn.Dropout(dropout)
                self.fc2 = nn.Linear(hidden, n_cls)

            def forward(self, x):
                # x: (batch, n_features) → (batch, 1, n_features)
                x = x.unsqueeze(1)
                x = torch.relu(self.bn1(self.conv1(x)))
                x = torch.relu(self.bn2(self.conv2(x)))
                x = self.pool(x).squeeze(-1)
                x = torch.relu(self.fc1(x))
                x = self.drop(x)
                return self.fc2(x)

        return CNN1D(n_features, n_classes, self.params["hidden_dim"], self.params["dropout"])

    def fit(self, X: np.ndarray, y: np.ndarray, **kwargs) -> Dict[str, Any]:
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset

        torch.manual_seed(self.params["random_state"])

        self.n_features = X.shape[1]
        self.n_classes = len(np.unique(y))

        self._device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"

        self.model = self._build_model(self.n_features, self.n_classes).to(self._device)

        X_t = torch.FloatTensor(X).to(self._device)
        y_t = torch.LongTensor(y).to(self._device)
        dataset = TensorDataset(X_t, y_t)
        loader = DataLoader(dataset, batch_size=self.params["batch_size"], shuffle=True)

        # Class weights for imbalanced data
        class_counts = np.bincount(y)
        weights = 1.0 / (class_counts + 1e-6)
        weights = weights / weights.sum() * len(weights)
        class_weights = torch.FloatTensor(weights).to(self._device)

        criterion = nn.CrossEntropyLoss(weight=class_weights)
        optimizer = torch.optim.Adam(self.model.parameters(), lr=self.params["learning_rate"])

        self.model.train()
        losses = []
        for epoch in range(self.params["n_epochs"]):
            epoch_loss = 0.0
            for batch_X, batch_y in loader:
                optimizer.zero_grad()
                out = self.model(batch_X)
                loss = criterion(out, batch_y)
                loss.backward()
                optimizer.step()
                epoch_loss += loss.item()
            losses.append(epoch_loss / len(loader))

        return {
            "final_loss": losses[-1] if losses else 0,
            "n_epochs": self.params["n_epochs"],
            "device": self._device,
            "n_features": self.n_features,
            "n_classes": self.n_classes,
        }

    def predict(self, X: np.ndarray) -> np.ndarray:
        import torch
        self.model.eval()
        with torch.no_grad():
            X_t = torch.FloatTensor(X).to(self._device)
            out = self.model(X_t)
            return out.argmax(dim=1).cpu().numpy()

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        import torch
        self.model.eval()
        with torch.no_grad():
            X_t = torch.FloatTensor(X).to(self._device)
            out = self.model(X_t)
            return torch.softmax(out, dim=1).cpu().numpy()

    def save(self, path: str) -> None:
        import torch
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        torch.save({
            "state_dict": self.model.state_dict(),
            "n_features": self.n_features,
            "n_classes": self.n_classes,
            "params": self.params,
        }, path)

    def load(self, path: str) -> None:
        import torch
        checkpoint = torch.load(path, map_location="cpu", weights_only=False)
        self.n_features = checkpoint["n_features"]
        self.n_classes = checkpoint["n_classes"]
        self.params = checkpoint["params"]
        self.model = self._build_model(self.n_features, self.n_classes)
        self.model.load_state_dict(checkpoint["state_dict"])
        self.model.eval()

    def get_params(self) -> Dict[str, Any]:
        return self.params.copy()


# ─── Transformer ──────────────────────────────────────────────────────────────

class TransformerDetector(BaseDetector):
    """
    Transformer encoder for feature-vector classification.

    Treats the feature vector as a sequence of tokens (each feature is one
    token with a learned embedding), then applies multi-head self-attention.

    Architecture:
        Input → Linear(d_model) → PositionalEncoding →
        TransformerEncoderLayer × n_layers → [CLS] token → FC(n_classes)
    """

    name = "transformer"

    def __init__(
        self,
        d_model: int = 64,
        n_heads: int = 4,
        n_layers: int = 2,
        dim_feedforward: int = 128,
        dropout: float = 0.1,
        n_epochs: int = 50,
        batch_size: int = 32,
        learning_rate: float = 1e-3,
        random_state: int = 42,
    ):
        self.params = {
            "d_model": d_model,
            "n_heads": n_heads,
            "n_layers": n_layers,
            "dim_feedforward": dim_feedforward,
            "dropout": dropout,
            "n_epochs": n_epochs,
            "batch_size": batch_size,
            "learning_rate": learning_rate,
            "random_state": random_state,
        }
        self.model = None
        self.n_classes = 2
        self.n_features = 0
        self._device = "cpu"

    def _build_model(self, n_features: int, n_classes: int):
        import torch
        import torch.nn as nn

        class FeatureTransformer(nn.Module):
            def __init__(self, n_feat, n_cls, d_model, n_heads, n_layers, dim_ff, dropout):
                super().__init__()
                self.d_model = d_model
                # Project each feature scalar to d_model dimensions
                self.input_proj = nn.Linear(1, d_model)
                # Learnable [CLS] token
                self.cls_token = nn.Parameter(torch.randn(1, 1, d_model))
                # Positional encoding (learnable)
                self.pos_embed = nn.Parameter(torch.randn(1, n_feat + 1, d_model))

                encoder_layer = nn.TransformerEncoderLayer(
                    d_model=d_model,
                    nhead=n_heads,
                    dim_feedforward=dim_ff,
                    dropout=dropout,
                    batch_first=True,
                )
                self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)
                self.classifier = nn.Linear(d_model, n_cls)

            def forward(self, x):
                # x: (batch, n_features)
                batch_size = x.shape[0]
                # → (batch, n_features, 1)
                x = x.unsqueeze(-1)
                # → (batch, n_features, d_model)
                x = self.input_proj(x)
                # Prepend [CLS] token
                cls = self.cls_token.expand(batch_size, -1, -1)
                x = torch.cat([cls, x], dim=1)
                # Add positional embedding
                x = x + self.pos_embed[:, :x.shape[1], :]
                # Transformer encoder
                x = self.encoder(x)
                # Use [CLS] token output
                cls_out = x[:, 0, :]
                return self.classifier(cls_out)

        return FeatureTransformer(
            n_features, n_classes,
            self.params["d_model"], self.params["n_heads"],
            self.params["n_layers"], self.params["dim_feedforward"],
            self.params["dropout"],
        )

    def fit(self, X: np.ndarray, y: np.ndarray, **kwargs) -> Dict[str, Any]:
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset

        torch.manual_seed(self.params["random_state"])

        self.n_features = X.shape[1]
        self.n_classes = len(np.unique(y))
        self._device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"

        self.model = self._build_model(self.n_features, self.n_classes).to(self._device)

        X_t = torch.FloatTensor(X).to(self._device)
        y_t = torch.LongTensor(y).to(self._device)
        dataset = TensorDataset(X_t, y_t)
        loader = DataLoader(dataset, batch_size=self.params["batch_size"], shuffle=True)

        class_counts = np.bincount(y)
        weights = 1.0 / (class_counts + 1e-6)
        weights = weights / weights.sum() * len(weights)
        class_weights = torch.FloatTensor(weights).to(self._device)

        criterion = nn.CrossEntropyLoss(weight=class_weights)
        optimizer = torch.optim.Adam(self.model.parameters(), lr=self.params["learning_rate"])

        self.model.train()
        losses = []
        for epoch in range(self.params["n_epochs"]):
            epoch_loss = 0.0
            for batch_X, batch_y in loader:
                optimizer.zero_grad()
                out = self.model(batch_X)
                loss = criterion(out, batch_y)
                loss.backward()
                optimizer.step()
                epoch_loss += loss.item()
            losses.append(epoch_loss / len(loader))

        return {
            "final_loss": losses[-1] if losses else 0,
            "n_epochs": self.params["n_epochs"],
            "device": self._device,
            "n_features": self.n_features,
            "n_classes": self.n_classes,
        }

    def predict(self, X: np.ndarray) -> np.ndarray:
        import torch
        self.model.eval()
        with torch.no_grad():
            X_t = torch.FloatTensor(X).to(self._device)
            out = self.model(X_t)
            return out.argmax(dim=1).cpu().numpy()

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        import torch
        self.model.eval()
        with torch.no_grad():
            X_t = torch.FloatTensor(X).to(self._device)
            out = self.model(X_t)
            return torch.softmax(out, dim=1).cpu().numpy()

    def save(self, path: str) -> None:
        import torch
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        torch.save({
            "state_dict": self.model.state_dict(),
            "n_features": self.n_features,
            "n_classes": self.n_classes,
            "params": self.params,
        }, path)

    def load(self, path: str) -> None:
        import torch
        checkpoint = torch.load(path, map_location="cpu", weights_only=False)
        self.n_features = checkpoint["n_features"]
        self.n_classes = checkpoint["n_classes"]
        self.params = checkpoint["params"]
        self.model = self._build_model(self.n_features, self.n_classes)
        self.model.load_state_dict(checkpoint["state_dict"])
        self.model.eval()

    def get_params(self) -> Dict[str, Any]:
        return self.params.copy()
