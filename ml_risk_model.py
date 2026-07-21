"""
core/ml_risk_model.py
------------------------
The "learns from text" half of the risk engine.

Why this exists alongside the rule engine (core/risk_engine.py):
- The rule engine is fast, explainable, and works offline/day-one with no
  training data -- but it only catches wording it already has a regex for.
  A scammer who rephrases ("kindly authenticate your identity code" instead
  of "share your OTP") slips through untouched.
- This module is a genuinely adaptive text classifier (TF-IDF vectorizer +
  SGDClassifier with log loss, i.e. online/incremental logistic
  regression). It is bootstrapped from core/seed_training_data.py so it
  is useful immediately, and then it *keeps learning*: every time a
  citizen or admin submits feedback ("this was actually a scam" / "this
  was actually fine") through POST /feedback, that example is appended to
  a durable feedback log and the model is retrained (partial_fit) on the
  spot. Nothing here is a static, one-time-trained demo model.

Design choices:
- SGDClassifier(loss="log_loss") supports .partial_fit(), so we can fold
  in new labeled examples cheaply without retraining from scratch on
  every request.
- TF-IDF vectorizer is fit once on the seed corpus (word n-grams 1-2,
  ~4000 features) and reused; it is NOT refit on every feedback event,
  since the SGD classifier already handles this vocabulary. We do
  periodically refit-from-scratch (see `retrain_from_log`) so genuinely
  new vocabulary from feedback also becomes available.
- Model + vectorizer + feedback log persist to disk (joblib / jsonl) so
  learning survives a restart -- this is the difference between a demo
  and an actually-adapting system.
- Blended into `core.risk_engine.HybridRiskEngine`, never used alone: ML
  probability nudges the rule-based score by at most +/-15 points, and
  is always shown to the citizen as a separate, labeled number so the
  system stays explainable rather than a black box.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import SGDClassifier
import joblib

from core.seed_training_data import get_training_pairs

MODEL_DIR = Path(__file__).parent / "model_store"
MODEL_DIR.mkdir(exist_ok=True)
VECTORIZER_PATH = MODEL_DIR / "vectorizer.joblib"
CLASSIFIER_PATH = MODEL_DIR / "classifier.joblib"
FEEDBACK_LOG_PATH = MODEL_DIR / "feedback_log.jsonl"

_lock = threading.Lock()


@dataclass
class MLPrediction:
    scam_probability: float  # 0.0 - 1.0
    label: str                # "likely_scam" | "likely_safe" | "uncertain"
    trained_on: int            # number of examples the model has seen total


class AdaptiveScamClassifier:
    """Thin wrapper that hides the sklearn plumbing from the rest of the app."""

    def __init__(self):
        self._vectorizer: TfidfVectorizer | None = None
        self._clf: SGDClassifier | None = None
        self._n_examples = 0
        self._load_or_bootstrap()

    # ---- lifecycle ---------------------------------------------------------
    def _load_or_bootstrap(self) -> None:
        if VECTORIZER_PATH.exists() and CLASSIFIER_PATH.exists():
            try:
                self._vectorizer = joblib.load(VECTORIZER_PATH)
                self._clf = joblib.load(CLASSIFIER_PATH)
                self._n_examples = self._count_feedback_rows() + len(get_training_pairs()[0])
                return
            except Exception:
                pass  # fall through to bootstrap if the saved model is corrupt/incompatible
        self._bootstrap()

    def _bootstrap(self) -> None:
        texts, labels = get_training_pairs()
        self._vectorizer = TfidfVectorizer(ngram_range=(1, 2), max_features=4000, min_df=1)
        X = self._vectorizer.fit_transform(texts)
        self._clf = SGDClassifier(loss="log_loss", alpha=1e-4, max_iter=5, random_state=42)
        self._clf.partial_fit(X, labels, classes=[0, 1])
        self._n_examples = len(texts)
        self._persist()

    def _persist(self) -> None:
        joblib.dump(self._vectorizer, VECTORIZER_PATH)
        joblib.dump(self._clf, CLASSIFIER_PATH)

    def _count_feedback_rows(self) -> int:
        if not FEEDBACK_LOG_PATH.exists():
            return 0
        with open(FEEDBACK_LOG_PATH, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)

    # ---- inference ----------------------------------------------------------
    def predict(self, text: str) -> MLPrediction:
        text = (text or "").strip()
        if not text or self._clf is None or self._vectorizer is None:
            return MLPrediction(scam_probability=0.0, label="uncertain", trained_on=self._n_examples)
        with _lock:
            X = self._vectorizer.transform([text])
            proba = float(self._clf.predict_proba(X)[0][1])
        if proba >= 0.7:
            label = "likely_scam"
        elif proba <= 0.3:
            label = "likely_safe"
        else:
            label = "uncertain"
        return MLPrediction(scam_probability=round(proba, 3), label=label, trained_on=self._n_examples)

    # ---- adaptive learning ---------------------------------------------------
    def record_feedback(self, text: str, is_scam: bool, source: str = "citizen") -> None:
        """Append a new labeled example and immediately fold it into the
        live model via partial_fit, so the very next prediction reflects it."""
        text = (text or "").strip()
        if not text:
            return
        with _lock:
            with open(FEEDBACK_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps({"text": text, "label": int(is_scam), "source": source}) + "\n")
            X = self._vectorizer.transform([text])
            self._clf.partial_fit(X, [int(is_scam)])
            self._n_examples += 1
            self._persist()

    def retrain_from_log(self) -> int:
        """Full refit (vectorizer + classifier) using seed data + every
        piece of feedback collected so far. Lets genuinely new vocabulary
        from feedback (not just seed data) become part of the feature
        space. Safe to call periodically (e.g. a nightly cron) or via the
        admin endpoint. Returns the number of examples trained on."""
        texts, labels = get_training_pairs()
        if FEEDBACK_LOG_PATH.exists():
            with open(FEEDBACK_LOG_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    texts.append(row["text"])
                    labels.append(int(row["label"]))
        with _lock:
            self._vectorizer = TfidfVectorizer(ngram_range=(1, 2), max_features=6000, min_df=1)
            X = self._vectorizer.fit_transform(texts)
            self._clf = SGDClassifier(loss="log_loss", alpha=1e-4, max_iter=8, random_state=42)
            self._clf.partial_fit(X, labels, classes=[0, 1])
            self._n_examples = len(texts)
            self._persist()
        return self._n_examples

    def status(self) -> dict:
        return {
            "trained_on_examples": self._n_examples,
            "feedback_examples_collected": self._count_feedback_rows(),
            "model_type": "TF-IDF + SGDClassifier(log_loss) -- online logistic regression",
        }


_singleton: AdaptiveScamClassifier | None = None
_singleton_lock = threading.Lock()


def get_classifier() -> AdaptiveScamClassifier:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = AdaptiveScamClassifier()
        return _singleton