"""Shared notebook helpers: PostgreSQL access and the NEXUS BI chart theme.

Every notebook imports this module so that all figures read from the same
database and share one visual system (the same palette the dashboard uses).
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# --- Palette (validated categorical order; see docs/architecture.md) ---------
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SURFACE = "#fcfcfb"
INK = {"primary": "#0b0b0b", "secondary": "#52514e", "muted": "#898781"}
GRID, AXIS = "#e1e0d9", "#c3c2b7"
MUTED_FILL = "#c3c2b7"   # de-emphasised marks (e.g. incomplete months)
SEQUENTIAL_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
DIVERGING = [[0.0, "#e34948"], [0.5, "#f0efec"], [1.0, "#2a78d6"]]   # red <-> neutral <-> blue

pio.templates["nexus"] = go.layout.Template(
    data=dict(bar=[go.Bar(marker=dict(cornerradius=4, line=dict(width=0)))]),
    layout=dict(
    font=dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif", size=13, color=INK["primary"]),
    title=dict(font=dict(size=17), x=0.01, xanchor="left"),
    paper_bgcolor=SURFACE,
    plot_bgcolor=SURFACE,
    colorway=SERIES,
    bargap=0.25,
    margin=dict(l=70, r=30, t=70, b=60),
    xaxis=dict(showgrid=False, linecolor=AXIS, ticks="", zeroline=False,
               tickfont=dict(color=INK["secondary"]), title=dict(font=dict(color=INK["secondary"]))),
    yaxis=dict(gridcolor=GRID, gridwidth=1, linecolor=AXIS, zeroline=False,
               tickfont=dict(color=INK["secondary"]), title=dict(font=dict(color=INK["secondary"]))),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                font=dict(color=INK["secondary"])),
    hoverlabel=dict(bgcolor="white", font=dict(color=INK["primary"])),
))
pio.templates.default = "nexus"

# Static PNG by default so the notebooks render on GitHub. For interactive charts
# in Jupyter set NEXUS_PLOTLY_RENDERER=notebook before starting the kernel.
pio.renderers.default = os.getenv("NEXUS_PLOTLY_RENDERER", "png")
if pio.renderers.default == "png":
    pio.renderers["png"].width, pio.renderers["png"].height, pio.renderers["png"].scale = 1000, 480, 1.5

pd.options.display.float_format = "{:,.2f}".format
pd.options.display.max_columns = 30

_engine: Engine | None = None


def engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(URL.create(
            "postgresql+psycopg",
            username=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            host=os.environ["POSTGRES_HOST"],
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=os.environ["POSTGRES_DB"],
        ))
    return _engine


def query(sql: str, **params) -> pd.DataFrame:
    """Run a parameterised SQL query (``:name`` placeholders) and return a DataFrame."""
    with engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


def brl(value: float) -> str:
    """Format a number as Brazilian reais for narrative text: R$ 1,234,567."""
    return f"R$ {value:,.0f}"
