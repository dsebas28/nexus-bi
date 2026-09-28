"""Customer churn: probability that a customer will NOT buy again within 180 days.

Definition and validation
-------------------------
* Churn at cut-off date T: the customer bought before T and makes no purchase in
  [T, T + 180 days).
* Features use only what was known at T. Reviews written and deliveries
  completed after T are ignored, and an undelivered order counts as late once
  its promised date has passed.
* Snapshots (derived from the data's reference date R):
      train  T1 = R - 360 days   ->  model selection by 5-fold stratified CV
      test   T2 = R - 180 days   ->  out-of-time evaluation of the chosen model
      score  R                   ->  the final model, refit on T2, scores everyone
* Returning is the rare class (~3%). Models are selected by ROC-AUC (independent
  of the threshold). The decision threshold maximises F1 of the returning class
  on the cross-validation predictions and is then applied unchanged to the test.
* No class re-weighting is used, so the scores stay usable as probabilities
  (reported with the Brier score).
* Risk factors: for each customer and feature, the change in churn probability
  when the feature is replaced by the value of a typical customer (the median).
  This works for any model and reads as "compared with a typical customer, this
  raises the risk by X points".
"""
from __future__ import annotations

import logging
from datetime import timedelta

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, average_precision_score, brier_score_loss, confusion_matrix,
                             precision_recall_fscore_support, roc_auc_score)
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from ..common import RANDOM_STATE, ModelRun, artifact_dir, query, reporting_period

log = logging.getLogger(__name__)

HORIZON_DAYS = 180
REGIONS = ["Norte", "Nordeste", "Centro-Oeste", "Sudeste", "Sul"]
MIN_FACTOR_IMPACT = 0.001   # ignore factors that move the probability by < 0.1 points


# -----------------------------------------------------------------------------
# Data and features
# -----------------------------------------------------------------------------

def load_history() -> pd.DataFrame:
    df = query("""
        SELECT o.customer_key, o.order_key, o.purchased_at, o.delivered_customer_at,
               o.estimated_delivery_date, v.revenue, v.freight, v.items, v.installments,
               v.main_payment_type, st.region, r.avg_score AS review_score, r.first_review_at
        FROM core.orders o
        JOIN analytics.v_orders v USING (order_key)
        JOIN core.states st ON st.state_code = v.state_code
        LEFT JOIN (SELECT order_key, avg(score) AS avg_score, min(review_created_at) AS first_review_at
                   FROM core.order_reviews GROUP BY order_key) r USING (order_key)
        WHERE v.is_valid_sale
    """)
    for col in ["purchased_at", "delivered_customer_at", "estimated_delivery_date", "first_review_at"]:
        df[col] = pd.to_datetime(df[col])
    return df.sort_values("purchased_at")


def build_features(history: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    """One row per customer with purchases before ``cutoff``, using only facts known then."""
    h = history[history["purchased_at"] < cutoff].copy()
    delivered = h["delivered_customer_at"] < cutoff
    h["late"] = np.where(delivered,
                         h["delivered_customer_at"].dt.normalize() > h["estimated_delivery_date"],
                         h["estimated_delivery_date"] < cutoff).astype(float)
    h["delivery_days"] = (h["delivered_customer_at"] - h["purchased_at"]).dt.days.where(delivered)
    h["review"] = h["review_score"].where(h["first_review_at"] < cutoff)
    h["paid_boleto"] = (h["main_payment_type"] == "boleto").astype(float)
    h["paid_voucher"] = (h["main_payment_type"] == "voucher").astype(float)

    g = h.groupby("customer_key")
    last = g.tail(1).set_index("customer_key")
    f = pd.DataFrame({
        "recency_days": (cutoff - g["purchased_at"].max()).dt.days,
        "tenure_days": (cutoff - g["purchased_at"].min()).dt.days,
        "frequency": g.size(),
        "monetary": g["revenue"].sum(),
        "avg_order_value": g["revenue"].mean(),
        "avg_items_per_order": g["items"].mean(),
        "freight_ratio": g["freight"].sum() / g["revenue"].sum().replace(0, np.nan),
        "max_installments": g["installments"].max(),
        "late_share": g["late"].mean(),
        "avg_delivery_days": g["delivery_days"].mean(),
        "avg_review": g["review"].mean(),
        "has_review": (g["review"].count() > 0).astype(float),
        "last_review": last["review"],
        "last_order_late": last["late"],
        "paid_boleto_share": g["paid_boleto"].mean(),
        "paid_voucher_share": g["paid_voucher"].mean(),
    })
    for region in REGIONS:
        f[f"region_{region.lower().replace('-', '_')}"] = (last["region"] == region).astype(float)
    return f


def build_labels(history: pd.DataFrame, customers: pd.Index, cutoff: pd.Timestamp) -> pd.Series:
    """1 = customer returned (bought again) within the horizon, 0 = churned."""
    window = history[(history["purchased_at"] >= cutoff)
                     & (history["purchased_at"] < cutoff + pd.Timedelta(days=HORIZON_DAYS))]
    return pd.Series(customers.isin(window["customer_key"]).astype(int), index=customers, name="returned")


# -----------------------------------------------------------------------------
# Models and evaluation
# -----------------------------------------------------------------------------

def candidates() -> dict:
    return {
        "Logistic regression": make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                                             LogisticRegression(max_iter=2000)),
        "Random forest": make_pipeline(SimpleImputer(strategy="median"),
                                       RandomForestClassifier(n_estimators=400, min_samples_leaf=25, n_jobs=-1,
                                                              random_state=RANDOM_STATE)),
        "XGBoost": XGBClassifier(n_estimators=300, max_depth=3, learning_rate=0.05, subsample=0.8,
                                 colsample_bytree=0.8, min_child_weight=5, eval_metric="auc",
                                 random_state=RANDOM_STATE, n_jobs=-1),
    }


def ranking_metrics(y: np.ndarray, p_return: np.ndarray) -> dict[str, float]:
    return {
        "roc_auc": roc_auc_score(y, p_return),
        "pr_auc_returning": average_precision_score(y, p_return),
        "brier": brier_score_loss(y, p_return),
    }


def best_threshold(y: np.ndarray, p_return: np.ndarray) -> float:
    """Threshold on P(return) that maximises F1 of the returning class."""
    grid = np.unique(np.quantile(p_return, np.linspace(0.50, 0.999, 300)))
    f1 = [precision_recall_fscore_support(y, p_return >= t, average="binary", zero_division=0)[2] for t in grid]
    return float(grid[int(np.argmax(f1))])


def threshold_metrics(y: np.ndarray, p_return: np.ndarray, threshold: float) -> dict[str, float]:
    """Metrics with CHURN as the positive class (predicted churn = P(return) < threshold)."""
    churn_true, churn_pred = 1 - y, (p_return < threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(churn_true, churn_pred, labels=[0, 1]).ravel()
    p, r, f1, _ = precision_recall_fscore_support(churn_true, churn_pred, average="binary", zero_division=0)
    pr, rr, f1r, _ = precision_recall_fscore_support(y, 1 - churn_pred, average="binary", zero_division=0)
    return {"precision_churn": p, "recall_churn": r, "f1_churn": f1,
            "precision_returning": pr, "recall_returning": rr, "f1_returning": f1r,
            "accuracy": accuracy_score(churn_true, churn_pred),
            "tn": tn, "fp": fp, "fn": fn, "tp": tp}


# -----------------------------------------------------------------------------
# Explanations
# -----------------------------------------------------------------------------

FEATURE_LABELS = {
    "recency_days": lambda v: f"No purchase in the last {v:.0f} days",
    "tenure_days": lambda v: f"Customer for {v:.0f} days",
    "frequency": lambda v: "Only one purchase so far" if v <= 1 else f"{v:.0f} purchases so far",
    "monetary": lambda v: f"Lifetime spend R$ {v:,.2f}",
    "avg_order_value": lambda v: f"Average order R$ {v:,.2f}",
    "avg_items_per_order": lambda v: f"{v:.1f} items per order",
    "freight_ratio": lambda v: f"Freight is {v:.0%} of order value",
    "max_installments": lambda v: f"Paid in up to {v:.0f} installments",
    "late_share": lambda v: f"{v:.0%} of orders delivered late",
    "avg_delivery_days": lambda v: f"Deliveries took {v:.0f} days on average",
    "avg_review": lambda v: f"Average review {v:.1f} stars",
    "has_review": lambda v: "Left a review" if v else "Never left a review",
    "last_review": lambda v: f"Last review {v:.0f} stars",
    "last_order_late": lambda v: "Last order arrived late" if v else "Last order arrived on time",
    "paid_boleto_share": lambda v: f"{v:.0%} of orders paid by boleto",
    "paid_voucher_share": lambda v: f"{v:.0%} of orders paid by voucher",
}


def describe(feature: str, value: float) -> str:
    if pd.isna(value):
        return f"{feature.replace('_', ' ').capitalize()}: unknown"
    if feature.startswith("region_"):
        return f"Lives in the {feature[7:].replace('_', '-').title()} region"
    return FEATURE_LABELS.get(feature, lambda v: f"{feature}: {v}")(value)


def risk_factors(model, X: pd.DataFrame, churn_prob: np.ndarray, top_n: int = 3) -> list[list[dict]]:
    """Per-customer features that raise churn probability the most vs a typical customer."""
    typical = X.median(numeric_only=True)
    impacts = {}
    for col in X.columns:
        X_typ = X.copy()
        X_typ[col] = typical[col]
        impacts[col] = churn_prob - (1 - model.predict_proba(X_typ)[:, 1])
    impact = pd.DataFrame(impacts, index=X.index).to_numpy()
    columns, values = list(X.columns), X.to_numpy()
    order = np.argsort(-impact, axis=1)[:, :top_n]

    factors = []
    for i, cols in enumerate(order):
        factors.append([{"feature": columns[j], "description": describe(columns[j], values[i, j]),
                         "impact_pts": round(float(impact[i, j]) * 100, 2)}
                        for j in cols if impact[i, j] >= MIN_FACTOR_IMPACT])
    return factors


# -----------------------------------------------------------------------------
# Orchestration
# -----------------------------------------------------------------------------

def run() -> dict:
    log.info("Churn model")
    _, _, reference_date = reporting_period()
    ref = pd.Timestamp(reference_date)
    t_test, t_train = ref - pd.Timedelta(days=HORIZON_DAYS), ref - pd.Timedelta(days=2 * HORIZON_DAYS)
    history = load_history()

    X_train = build_features(history, t_train)
    y_train = build_labels(history, X_train.index, t_train)
    X_test = build_features(history, t_test)
    y_test = build_labels(history, X_test.index, t_test)
    log.info("  train snapshot %s: %s customers, %.2f%% returned", t_train.date(), f"{len(X_train):,}",
             y_train.mean() * 100)
    log.info("  test  snapshot %s: %s customers, %.2f%% returned", t_test.date(), f"{len(X_test):,}",
             y_test.mean() * 100)

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=RANDOM_STATE)
    rows, oof = [], {}
    for name, model in candidates().items():
        oof[name] = cross_val_predict(model, X_train, y_train, cv=cv, method="predict_proba")[:, 1]
        for metric, value in ranking_metrics(y_train, oof[name]).items():
            rows.append((name, "cv_train", metric, value))
        log.info("  %-20s CV ROC-AUC %.4f", name, rows[-3][3])

    cv_auc = {name: roc_auc_score(y_train, p) for name, p in oof.items()}
    selected = max(cv_auc, key=cv_auc.get)
    threshold = best_threshold(y_train.to_numpy(), oof[selected])

    # Out-of-time evaluation: every candidate refit on the full train snapshot.
    fitted = {}
    for name, model in candidates().items():
        fitted[name] = model.fit(X_train, y_train)
        p = fitted[name].predict_proba(X_test)[:, 1]
        for metric, value in ranking_metrics(y_test, p).items():
            rows.append((name, "out_of_time_test", metric, value))
    p_test = fitted[selected].predict_proba(X_test)[:, 1]
    test_metrics = threshold_metrics(y_test.to_numpy(), p_test, threshold)
    rows += [(selected, "out_of_time_test", m, v) for m, v in test_metrics.items()]

    # Reference points: a one-feature rule and the trivial "everyone churns" prediction.
    rows.append(("Baseline: recency only", "out_of_time_test", "roc_auc",
                 roc_auc_score(y_test, -X_test["recency_days"])))
    always = threshold_metrics(y_test.to_numpy(), np.zeros(len(y_test)), 0.5)
    rows += [("Baseline: always churn", "out_of_time_test", m, always[m]) for m in ("accuracy", "f1_churn",
                                                                                   "recall_returning")]
    evaluations = pd.DataFrame(rows, columns=["candidate", "split", "metric", "value"])
    log.info("  selected %s | test ROC-AUC %.4f | threshold P(return) >= %.4f", selected,
             roc_auc_score(y_test, p_test), threshold)

    # Final model: refit on the most recent labelled snapshot, score everyone at R.
    final = candidates()[selected].fit(X_test, y_test)
    joblib.dump(final, artifact_dir("churn") / "churn_model.joblib")
    X_now = build_features(history, ref)
    X_now = X_now[X_now.index.isin(query("SELECT customer_key FROM analytics.v_customer_summary")["customer_key"])]
    churn_prob = 1 - final.predict_proba(X_now)[:, 1]
    bands = pd.qcut(pd.Series(churn_prob).rank(method="first"), 3, labels=["Low", "Medium", "High"]).astype(str)
    log.info("  computing risk factors for %s customers", f"{len(X_now):,}")
    scores = pd.DataFrame({
        "customer_key": X_now.index,
        "churn_probability": np.round(churn_prob, 5),
        "risk_band": bands.to_numpy(),
        "top_factors": risk_factors(final, X_now, churn_prob),
    })

    with ModelRun("churn", selected, reference_date,
                  notes=f"Churn = no purchase within {HORIZON_DAYS} days. Selected by CV ROC-AUC.") as run:
        run.params = {"horizon_days": HORIZON_DAYS, "train_cutoff": str(t_train.date()),
                      "test_cutoff": str(t_test.date()), "threshold_p_return": threshold,
                      "features": list(X_train.columns), "risk_bands": "tertiles of churn probability"}
        run.metrics = {"selected_model": selected, "cv_roc_auc": cv_auc,
                       "test_roc_auc": roc_auc_score(y_test, p_test),
                       "test_return_rate": float(y_test.mean()), **test_metrics}
        run.add_evaluations(evaluations)
        run.write("ml.churn_scores", scores)
    return {"evaluations": evaluations, "selected": selected, "threshold": threshold, "scores": scores,
            "X_test": X_test, "y_test": y_test, "p_test": p_test, "model": final}
