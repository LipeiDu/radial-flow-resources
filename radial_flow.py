"""Read published results and replot Figures 1–3 of arXiv:2508.07184.

Only saved numerical results are used; no particle-level analysis is performed.
"""

from __future__ import annotations

import ast
import csv
from pathlib import Path
import re

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np


def _literal(node):
    """Interpret the limited data syntax used by the Zenodo text export."""
    if isinstance(node, ast.Constant) and type(node.value) in (int, float, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in ("nan", "inf"):
        return float(node.id)
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_literal(item) for item in node.elts]
    if isinstance(node, ast.Dict):
        keys = [_literal(key) for key in node.keys]
        if any(not isinstance(key, str) for key in keys) or len(set(keys)) != len(keys):
            raise ValueError("Expected unique string keys")
        return dict(zip(keys, (_literal(value) for value in node.values)))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _literal(node.operand)
        if type(value) not in (int, float):
            raise ValueError("Expected a numeric sign operand")
        return -value if isinstance(node.op, ast.USub) else value
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "array" and len(node.args) == 1 and not node.keywords):
        value = _literal(node.args[0])
        if not isinstance(value, list):
            raise ValueError("array() requires a numeric list")
        return np.asarray(value, dtype=float)
    raise ValueError(f"Unsupported result-file syntax: {type(node).__name__}")


def read_zenodo(path):
    """Read a sectioned Zenodo .dat file without executing its contents.

    Returns a dictionary keyed by section name, e.g.
    ``result['v0_pT_fluctuation_based']['ch']['v0_pT_n2']``.
    Python/NumPy-style ``array([...])`` values become NumPy arrays.
    """
    text = Path(path).read_text(encoding="utf-8")
    headings = list(re.finditer(r"^([A-Za-z][A-Za-z0-9_]*):\s*$", text, re.MULTILINE))
    if not headings or text[:headings[0].start()].strip():
        raise ValueError("Expected a sectioned Zenodo result file")
    result = {}
    for i, heading in enumerate(headings):
        name = heading.group(1)
        if name in result:
            raise ValueError(f"Duplicate section: {name}")
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        value = _literal(ast.parse(text[heading.end():end].strip(), mode="eval").body)
        if not isinstance(value, dict):
            raise ValueError(f"Expected a mapping in section {name}")
        result[name] = value
    return result


def read_table(path):
    """Read a supplied CSV. Blank numeric cells mean unavailable, never zero."""
    numeric = {"pt_GeV", "pt_low_GeV", "pt_high_GeV", "value", "error_low", "error_high",
               "stat_error_low", "stat_error_high", "sys_error_low", "sys_error_high"}
    with Path(path).open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        for key in numeric & row.keys():
            row[key] = float(row[key]) if row[key] else None
        for key in ("pt_GeV", "value"):
            if row.get(key) is None or not np.isfinite(row[key]):
                raise ValueError(f"Missing/nonfinite {key} in {path}")
        for key in ("error_low", "error_high"):
            if row.get(key) is not None and (not np.isfinite(row[key]) or row[key] < 0):
                raise ValueError(f"Invalid error magnitude in {path}")
    return rows


def load_inputs(directory=None):
    """Load all bundled inputs, relative to this module unless specified."""
    root = Path(directory) if directory is not None else Path(__file__).resolve().parent
    model_dir = root / "data" / "model"
    return {
        "cut": {experiment: read_zenodo(model_dir / filename) for experiment, filename in {
            "ALICE": "observables_ALICE_5020_bulk_30_40_centrality_results.dat",
            "ATLAS": "observables_ATLAS_5020_bulk_30_40_centrality_results.dat",
        }.items()},
        "figure1": read_table(model_dir / "figure1_model.csv"),
        "nocut": read_table(model_dir / "figure2_nocut.csv"),
        "experiment": read_table(root / "data" / "experiment.csv"),
    }


def _select(rows, **criteria):
    selected = [row for row in rows if all(row.get(key) == value for key, value in criteria.items())]
    if not selected:
        raise ValueError(f"No rows for {criteria}")
    return sorted(selected, key=lambda row: row["pt_GeV"])


def _xy(rows):
    return np.array([r["pt_GeV"] for r in rows]), np.array([r["value"] for r in rows])


def _points(ax, rows, *, color, marker, label, scale=1):
    x, y = _xy(rows)
    has_errors = [r.get("error_low") is not None and r.get("error_high") is not None for r in rows]
    if any(has_errors) and not all(has_errors):
        raise ValueError("Mixed availability of error bars in one plotted series")
    if all(has_errors):
        error = np.array([[r["error_low"] for r in rows], [r["error_high"] for r in rows]])
        ax.errorbar(x, y * scale, yerr=error * scale, fmt=marker, color=color,
                    markersize=4, elinewidth=0.9, capsize=0, label=label, zorder=4)
    else:
        ax.plot(x, y * scale, marker=marker, linestyle="none", color=color,
                markersize=4, label=label, zorder=4)


def _v0(result, method, species):
    section = result[f"v0_pT_{method}_based"]
    x = np.asarray(section["pT_bin_v0"], dtype=float)
    y = np.asarray(section[species]["v0_pT_n2"], dtype=float)
    error = section[species].get("v0_pT_n2_err")
    if x.ndim != 1 or y.shape != x.shape or not np.all(np.isfinite([x, y])):
        raise ValueError("Invalid v0 coordinates or values")
    if error is not None:
        error = np.asarray(error, dtype=float)
        if error.shape != y.shape or np.any(error < 0) or not np.all(np.isfinite(error)):
            raise ValueError("Invalid v0 uncertainty")
    return x, y, error


def _model_line(ax, x, y, error, color, linestyle):
    ax.plot(x, y, color=color, linestyle=linestyle, linewidth=1.6)
    if error is not None:
        ax.fill_between(x, y - error, y + error, color=color, alpha=0.18, linewidth=0)


def _style():
    return plt.rc_context({
        "font.family": "DejaVu Sans", "font.size": 10,
        "axes.labelsize": 11, "axes.linewidth": 0.9,
        "xtick.direction": "in", "ytick.direction": "in",
        "xtick.top": True, "ytick.right": True,
        "legend.frameon": False, "figure.dpi": 120, "savefig.dpi": 220,
    })


def plot_figure1(inputs):
    """Published Fig. 1: invariant spectra and differential elliptic flow."""
    model, data = inputs["figure1"], inputs["experiment"]
    with _style():
        fig, axes = plt.subplots(2, 1, figsize=(5.2, 7.0), layout="constrained")
        ax = axes[0]
        ax.set(yscale="log", xlim=(0, 3.5), ylim=(5e-4, 5e4),
               ylabel=r"$\mathrm{d}^2N/(2\pi p_T\,\mathrm{d}p_T\,\mathrm{d}y)$ [GeV$^{-2}$]")
        for sp, scale, color, marker, label in [
            ("pi", 100, "tab:red", "o", r"$\pi^+\times100$"),
            ("k", 10, "tab:green", "s", r"$K^+\times10$"),
            ("p", 1, "tab:blue", "^", r"$p$"),
        ]:
            rows = _select(model, observable="spectra", species=sp, centrality="30-40")
            x, y = _xy(rows)
            ax.plot(x, y * scale, color=color)
            rows = _select(data, observable="spectra", species=sp, centrality="30-40")
            _points(ax, rows, color=color, marker=marker, label=label, scale=scale)
        ax.text(0.04, 0.95, "(a)  Au+Au 200 GeV", transform=ax.transAxes, va="top")
        ax.text(0.06, 0.08, "30–40%  |  PHENIX", transform=ax.transAxes)
        ax.legend(loc="upper right")

        ax = axes[1]
        ax.set(xlim=(0, 3.5), ylim=(-0.01, 0.3), xlabel=r"$p_T$ [GeV]", ylabel=r"$v_2(p_T)$")
        for centrality, color, marker in [("0-5", "tab:red", "o"), ("30-40", "tab:green", "s")]:
            x, y = _xy(_select(model, observable="v2", species="ch", centrality=centrality))
            ax.plot(x, y, color=color)
            _points(ax, _select(data, observable="v2", species="ch", centrality=centrality),
                    color=color, marker=marker, label=centrality.replace("-", "–") + "%")
        ax.text(0.04, 0.95, "(b)  Charged hadrons  |  STAR", transform=ax.transAxes, va="top")
        ax.legend(loc="upper left", bbox_to_anchor=(0.02, 0.87))
    return fig


def plot_figure2(inputs):
    """Published Fig. 2: charged v0 with experimental and no-low-pT cuts."""
    with _style():
        fig, ax = plt.subplots(figsize=(5.4, 4.0), layout="constrained")
        for experiment, color, marker in [("ALICE", "tab:purple", "o"), ("ATLAS", "tab:green", "s")]:
            _model_line(ax, *_v0(inputs["cut"][experiment], "fluctuation", "ch"), color, "-")
            rows = _select(inputs["nocut"], experiment=experiment, species="ch")
            x, y = _xy(rows)
            error = np.array([r["error_high"] for r in rows])
            _model_line(ax, x, y, error, color, "--")
            rows = _select(inputs["experiment"], observable="v0", experiment=experiment, species="ch")
            _points(ax, rows, color=color, marker=marker, label=experiment)
        ax.axhline(0, color="0.65", linewidth=0.7, linestyle=":")
        ax.set(xlim=(0, 4.4), xlabel=r"$p_T$ [GeV]", ylabel=r"$v_0(p_T)$")
        ax.text(0.04, 0.95, "30–40% Pb+Pb 5.02 TeV", transform=ax.transAxes, va="top")
        data_legend = ax.legend(loc="upper left", bbox_to_anchor=(0.03, 0.88))
        ax.add_artist(data_legend)
        ax.legend(handles=[Line2D([], [], color="0.2", label=r"Experimental $p_T$ cut"),
                           Line2D([], [], color="0.2", linestyle="--", label=r"$p_T\to0$")],
                  loc="lower right", fontsize=9)
    return fig


def plot_figure3(inputs):
    """Published Fig. 3: identified v0 and the two saved analysis forms."""
    with _style():
        fig, ax = plt.subplots(figsize=(5.4, 4.0), layout="constrained")
        for species, color, marker, label in [
            ("pi", "tab:red", "o", r"$\pi^\pm$"),
            ("k", "tab:green", "s", r"$K^\pm$"),
            ("p", "tab:blue", "D", r"$p/\bar p$"),
        ]:
            for method, linestyle in [("fluctuation", "-"), ("covariance", "--")]:
                _model_line(ax, *_v0(inputs["cut"]["ALICE"], method, species), color, linestyle)
            rows = _select(inputs["experiment"], observable="v0", experiment="ALICE", species=species)
            _points(ax, rows, color=color, marker=marker, label=label)
        ax.axhline(0, color="0.65", linewidth=0.7, linestyle=":")
        ax.set(xlim=(0, 4.3), xlabel=r"$p_T$ [GeV]", ylabel=r"$v_0(p_T)$")
        ax.text(0.04, 0.95, "30–40% Pb+Pb 5.02 TeV  |  ALICE", transform=ax.transAxes, va="top")
        data_legend = ax.legend(loc="upper left", bbox_to_anchor=(0.03, 0.88))
        ax.add_artist(data_legend)
        ax.legend(handles=[Line2D([], [], color="0.2", label="Fluctuation form"),
                           Line2D([], [], color="0.2", linestyle="--", label="Covariance form")],
                  loc="lower right", fontsize=9)
    return fig


def save_figures(directory="figures", inputs=None):
    """Generate all three PNGs and return their paths."""
    inputs = load_inputs() if inputs is None else inputs
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for number, plotter in enumerate((plot_figure1, plot_figure2, plot_figure3), 1):
        fig = plotter(inputs)
        path = directory / f"figure{number}.png"
        fig.savefig(path)
        plt.close(fig)
        paths.append(path)
    return paths


if __name__ == "__main__":
    for path in save_figures():
        print(path)
