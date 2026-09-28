"""Daily revenue forecasting with model comparison by rolling-origin backtesting.

Design
------
* Target: daily revenue of sales, forecast ``HORIZON`` days ahead of an origin.
* ML models are trained with the *direct* strategy: one row per (origin, horizon
  day). They predict the ratio between the day's revenue and the mean of the 28
  days before the origin. This makes them scale-free, so tree models can follow
  growth they never saw in absolute terms.
* Candidates: two baselines (seasonal naive, 4-week weekday average), Ridge,
  Random Forest and XGBoost.
* Evaluation: rolling-origin backtest. Every ``BACKTEST_STEP`` days a model is
  trained only on data before the origin and forecasts the next ``HORIZON`` days.
  The model with the lowest MAE is selected.
* Uncertainty: an empirical 80% interval from the selected model's backtest
  errors (10th-90th percentile of actual/forecast, per week of horizon). It is
  an honest range, not a guarantee.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, mean_squared_error, r2_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

from ..common import RANDOM_STATE, ModelRun, artifact_dir, query, reporting_period

log = logging.getLogger(__name__)

HORIZON = 56          # 8 weeks
BACKTEST_STEP = 28    # a new origin every 4 weeks
BACKTEST_FOLDS = 7
MIN_HISTORY = 56      # days of history needed before the first training origin


# -----------------------------------------------------------------------------
# Data and features
# -----------------------------------------------------------------------------

def load_daily_revenue() -> pd.Series:
    df = query("""
        SELECT c.date_key, coalesce(sum(s.revenue), 0) AS revenue
        FROM core.calendar c
        LEFT JOIN analytics.v_sales_items s ON s.purchase_date = c.date_key
        WHERE c.date_key >= (SELECT first_month FROM analytics.v_reporting_period)
          AND c.date_key <  (SELECT reference_date FROM analytics.v_reporting_period)
        GROUP BY c.date_key
        ORDER BY c.date_key
    """)
    return df.set_index(pd.to_datetime(df["date_key"]))["revenue"].astype(float).asfreq("D")


def load_holidays() -> set[pd.Timestamp]:
    return set(pd.to_datetime(query("SELECT date_key FROM core.calendar WHERE is_holiday")["date_key"]))


def black_friday(year: int) -> pd.Timestamp:
    """Friday after the fourth Thursday of November."""
    nov1 = pd.Timestamp(year, 11, 1)
    first_thursday = nov1 + pd.Timedelta(days=(3 - nov1.dayofweek) % 7)
    return first_thursday + pd.Timedelta(days=22)


def calendar_features(dates: pd.DatetimeIndex, holidays: set[pd.Timestamp]) -> pd.DataFrame:
    bf = {y: black_friday(y) for y in set(dates.year)}
    days_to_bf = np.array([(d - bf[d.year]).days for d in dates])
    features = pd.DataFrame({
        "day_of_month": dates.day,
        "month": dates.month,
        "is_holiday": [d in holidays for d in dates],
        "black_friday_week": (days_to_bf >= -4) & (days_to_bf <= 3),   # Mon before BF to Cyber Monday
        "december_pre_christmas": (dates.month == 12) & (dates.day <= 20),
    }, index=dates).astype(float)
    for dow in range(7):
        features[f"dow_{dow}"] = (dates.dayofweek == dow).astype(float)
    return features


def origin_features(y: pd.Series, origin: pd.Timestamp) -> dict[str, float]:
    """Information available at the origin (data strictly before ``origin``).

    A momentum feature (last 28 days vs the 28 before) was tested and removed:
    the business grew fast in 2017 and plateaued in 2018, so models that learned
    to extrapolate momentum over-forecast 2018 by ~15-18% in the backtest.
    """
    past = y[:origin - pd.Timedelta(days=1)]
    level_28 = past[-28:].mean()
    return {
        "level_28": level_28,
        "level_7_ratio": past[-7:].mean() / level_28,
    }


def make_supervised(y: pd.Series, origins: list[pd.Timestamp], holidays: set[pd.Timestamp],
                    horizon: int = HORIZON, with_target: bool = True) -> pd.DataFrame:
    frames = []
    for origin in origins:
        dates = pd.date_range(origin, periods=horizon, freq="D")
        block = calendar_features(dates, holidays)
        for key, value in origin_features(y, origin).items():
            block[key] = value
        block["horizon"] = np.arange(1, horizon + 1)
        block["origin"] = origin
        if with_target:
            block["actual"] = y.reindex(dates).to_numpy()
        frames.append(block)
    return pd.concat(frames)


def _xy(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    X = frame.drop(columns=["origin", "actual", "level_28"], errors="ignore")
    return X, frame["actual"] / frame["level_28"]


# -----------------------------------------------------------------------------
# Candidates
# -----------------------------------------------------------------------------

def ml_candidates() -> dict:
    return {
        "Ridge regression": make_pipeline(StandardScaler(), Ridge(alpha=1.0)),
        "Random forest": RandomForestRegressor(n_estimators=300, min_samples_leaf=5, n_jobs=-1,
                                               random_state=RANDOM_STATE),
        "XGBoost": XGBRegressor(n_estimators=400, max_depth=4, learning_rate=0.05, subsample=0.8,
                                colsample_bytree=0.8, random_state=RANDOM_STATE, n_jobs=-1),
    }


def baseline_forecast(y: pd.Series, origin: pd.Timestamp, method: str, horizon: int = HORIZON) -> np.ndarray:
    past = y[:origin - pd.Timedelta(days=1)]
    dates = pd.date_range(origin, periods=horizon, freq="D")
    if method == "Seasonal naive (last week)":
        last_week = past[-7:]
        by_dow = dict(zip(last_week.index.dayofweek, last_week.to_numpy()))
    else:  # "Weekday average (4 weeks)"
        last4 = past[-28:]
        by_dow = last4.groupby(last4.index.dayofweek).mean().to_dict()
    return np.array([by_dow[d.dayofweek] for d in dates])


BASELINES = ["Seasonal naive (last week)", "Weekday average (4 weeks)"]


def training_origins(y: pd.Series, cutoff: pd.Timestamp, horizon: int = HORIZON) -> list[pd.Timestamp]:
    """Every origin whose full horizon ends before ``cutoff`` (no look-ahead)."""
    first = y.index[0] + pd.Timedelta(days=MIN_HISTORY)
    last = cutoff - pd.Timedelta(days=horizon)
    return list(pd.date_range(first, last, freq="D"))


# -----------------------------------------------------------------------------
# Backtest, selection and final forecast
# -----------------------------------------------------------------------------

def metrics(actual: np.ndarray, pred: np.ndarray) -> dict[str, float]:
    positive = actual > 0   # MAPE is undefined on zero-revenue days
    return {
        "MAE": mean_absolute_error(actual, pred),
        "RMSE": float(np.sqrt(mean_squared_error(actual, pred))),
        "MAPE": mean_absolute_percentage_error(actual[positive], pred[positive]) * 100,
        "R2": r2_score(actual, pred),
        "Bias %": (pred.sum() / actual.sum() - 1) * 100,
    }


def backtest(y: pd.Series, holidays: set[pd.Timestamp]) -> pd.DataFrame:
    end = y.index[-1] + pd.Timedelta(days=1)
    origins = [end - pd.Timedelta(days=HORIZON + k * BACKTEST_STEP) for k in range(BACKTEST_FOLDS)][::-1]
    rows = []
    for origin in origins:
        test = make_supervised(y, [origin], holidays)
        train = make_supervised(y, training_origins(y, origin), holidays)
        X_train, r_train = _xy(train)
        X_test, _ = _xy(test)
        preds = {name: baseline_forecast(y, origin, name) for name in BASELINES}
        for name, model in ml_candidates().items():
            model.fit(X_train, r_train)
            preds[name] = model.predict(X_test) * test["level_28"].to_numpy()
        for name, p in preds.items():
            rows.append(pd.DataFrame({"model": name, "origin": origin, "date": test.index,
                                      "horizon": test["horizon"].to_numpy(),
                                      "actual": test["actual"].to_numpy(), "forecast": p}))
        log.info("  backtest origin %s: trained on %s origins", origin.date(), f"{len(train) // HORIZON:,}")
    return pd.concat(rows, ignore_index=True)


def summarize_backtest(bt: pd.DataFrame) -> pd.DataFrame:
    out = {}
    for name, g in bt.groupby("model"):
        m = metrics(g["actual"].to_numpy(), g["forecast"].to_numpy())
        totals = g.groupby("origin")[["actual", "forecast"]].sum()
        m["Horizon total APE"] = float((totals["forecast"] / totals["actual"] - 1).abs().mean() * 100)
        out[name] = m
    return pd.DataFrame(out).T.sort_values("MAE")


def interval_factors(bt_selected: pd.DataFrame) -> pd.DataFrame:
    """10th/90th percentile of actual/forecast by week of horizon."""
    week = (bt_selected["horizon"] - 1) // 7
    ratio = bt_selected["actual"] / bt_selected["forecast"]
    return ratio.groupby(week).quantile([0.10, 0.90]).unstack().rename(columns={0.10: "low", 0.90: "high"})


def interval_coverage(bt_selected: pd.DataFrame) -> float:
    """Leave-one-origin-out check: intervals built without a fold, tested on it.

    Measuring coverage on the same errors used to build the interval would give
    ~80% by construction, so each fold is evaluated with factors from the others.
    """
    hits = []
    for origin, fold in bt_selected.groupby("origin"):
        f = interval_factors(bt_selected[bt_selected["origin"] != origin])
        week = (fold["horizon"] - 1) // 7
        low, high = fold["forecast"] * week.map(f["low"]), fold["forecast"] * week.map(f["high"])
        hits.append(((fold["actual"] >= low) & (fold["actual"] <= high)).to_numpy())
    return float(np.concatenate(hits).mean())


def final_forecast(y: pd.Series, holidays: set[pd.Timestamp], selected: str,
                   factors: pd.DataFrame) -> tuple[pd.DataFrame, object]:
    origin = y.index[-1] + pd.Timedelta(days=1)
    dates = pd.date_range(origin, periods=HORIZON, freq="D")
    model = None
    if selected in BASELINES:
        point = baseline_forecast(y, origin, selected)
    else:
        train = make_supervised(y, training_origins(y, origin), holidays)
        X_train, r_train = _xy(train)
        model = ml_candidates()[selected].fit(X_train, r_train)
        future = make_supervised(y, [origin], holidays, with_target=False)
        X_future, _ = _xy(future.assign(actual=np.nan))
        point = model.predict(X_future) * future["level_28"].to_numpy()
    week = np.arange(HORIZON) // 7
    low = point * factors["low"].reindex(week).to_numpy()
    high = point * factors["high"].reindex(week).to_numpy()
    out = pd.DataFrame({"forecast_date": dates.date, "forecast": point.round(2),
                        "lower_80": np.minimum(low, point).round(2), "upper_80": np.maximum(high, point).round(2)})
    return out, model


def run() -> dict:
    log.info("Sales forecasting")
    _, _, reference_date = reporting_period()
    y = load_daily_revenue()
    holidays = load_holidays()

    bt = backtest(y, holidays)
    comparison = summarize_backtest(bt)
    selected = comparison.index[0]
    log.info("\n%s", comparison.round(2).to_string())
    log.info("  selected: %s (lowest backtest MAE)", selected)

    factors = interval_factors(bt[bt["model"] == selected])
    coverage = interval_coverage(bt[bt["model"] == selected])
    log.info("  80%% interval: out-of-fold coverage %.1f%%", coverage * 100)
    forecast, model = final_forecast(y, holidays, selected, factors)
    if model is not None:
        joblib.dump(model, artifact_dir("forecasting") / "sales_forecast_model.joblib")

    evaluations = (comparison.reset_index(names="candidate")
                   .melt(id_vars="candidate", var_name="metric", value_name="value")
                   .assign(split="rolling_backtest"))
    with ModelRun("sales_forecast", selected, reference_date,
                  notes="Selected by lowest MAE in a rolling-origin backtest.") as run:
        run.params = {"horizon_days": HORIZON, "backtest_folds": BACKTEST_FOLDS, "backtest_step_days": BACKTEST_STEP,
                      "target": "daily revenue / mean revenue of the 28 days before the origin",
                      "interval": "empirical 10th-90th percentile of actual/forecast in backtest, by horizon week",
                      "training_days": [str(y.index[0].date()), str(y.index[-1].date())]}
        run.metrics = {"selected_model": selected, **comparison.loc[selected].round(4).to_dict(),
                       "interval_coverage_out_of_fold": round(float(coverage), 4),
                       "forecast_total": round(float(forecast["forecast"].sum()), 2)}
        run.add_evaluations(evaluations)
        run.write("ml.sales_forecast", forecast)
    return {"comparison": comparison, "selected": selected, "forecast": forecast, "backtest": bt,
            "interval_coverage": coverage}
