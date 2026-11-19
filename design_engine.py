"""
engine.py - all algorithms, no Streamlit.  Each block is small and independent.

  1. data helpers        ensure_cols / load_csv / validate_rows
  2. carrier ranking     rank_carriers   (transparent rules from knowledge.py)
  3. design + priors     design_bounds / prior_frame
  4. Surrogate           literature prior + Gaussian-process correction learned from your rows
  5. specs + scoring     desirability, probability that all specs are met
  6. optimiser           propose (Bayesian optimisation, batch, explore -> exploit)
  7. extras              recipe, plan, virtual_lab (demo only), lodo (validation), precedents
"""
from __future__ import annotations

import math
import warnings
import zlib
import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel as C, Matern, WhiteKernel

import design_knowledge as K

warnings.filterwarnings("ignore", category=ConvergenceWarning)

NAN = float("nan")
DESIGN_COLS = K.DESIGN_COLS
COLUMNS = ["drug", "mw", "logp", "pka", "ionization", "solubility_mg_ml", "vesicle",
           "modifier_pct", "lipid_conc_pct", "drug_to_lipid", "size_nm", "pdi", "zeta_mv", "ee_pct",
           "source", "doi", "verified", "round", "notes"]
_NUM = ["mw", "logp", "pka", "solubility_mg_ml", "modifier_pct", "lipid_conc_pct", "drug_to_lipid",
        "size_nm", "pdi", "zeta_mv", "ee_pct", "round"]
_STR = ["drug", "ionization", "vesicle", "source", "doi", "notes"]
LIMITS = dict(mw=(50, 5000), logp=(-8, 12), pka=(0, 14), solubility_mg_ml=(1e-6, 2000), modifier_pct=(0, 80),
              lipid_conc_pct=(0, 30), drug_to_lipid=(0.001, 5), size_nm=(10, 5000), pdi=(0, 1),
              zeta_mv=(-100, 100), ee_pct=(0, 100))


@dataclass(frozen=True)
class Drug:
    name: str = "Drug"
    mw: float = 400.0
    logp: float = 2.0
    ionization: str = "Neutral"
    pka: float = NAN
    solubility: float = 0.1          # mg/mL
    labile: bool = False
    phyto: bool = False              # polar phytoconstituent (polyphenol, flavonoid ...)

    def props(self) -> dict:
        return dict(drug=self.name, mw=self.mw, logp=self.logp, pka=self.pka,
                    ionization=self.ionization, solubility_mg_ml=self.solubility)

    def key(self) -> tuple:
        return (self.name, self.mw, self.logp, self.ionization, -1.0 if math.isnan(self.pka) else self.pka,
                self.solubility, self.labile, self.phyto)

    @staticmethod
    def from_key(k) -> "Drug":
        n, mw, lp, ion, pka, sol, lab, phy = k
        return Drug(n, mw, lp, ion, NAN if pka < 0 else pka, sol, lab, phy)


@dataclass(frozen=True)
class Specs:
    size_lo: float = 0.0
    size_hi: float = 200.0
    pdi_max: float = 0.30
    zeta_min: float = 20.0           # |zeta| >= this (mV)
    ee_min: float = 70.0


# ------------------------------------------------------------------ 1. data helpers
def _to_bool(x) -> bool:
    return str(x).strip().lower() in ("true", "1", "yes", "y", "t")


def ensure_cols(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    for c in COLUMNS:
        if c not in d.columns:
            d[c] = np.nan if c in _NUM else (False if c == "verified" else "")
    for c in _NUM:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    for c in _STR:
        d[c] = d[c].fillna("").astype(str)
    d["verified"] = d["verified"].map(_to_bool).astype(bool)
    return d[COLUMNS + [c for c in d.columns if c not in COLUMNS]]


def empty_rows() -> pd.DataFrame:
    return ensure_cols(pd.DataFrame())


def concat_rows(frames) -> pd.DataFrame:
    frames = [f for f in frames if f is not None and len(f)]
    return pd.concat(frames, ignore_index=True) if frames else empty_rows()


def load_csv(path) -> pd.DataFrame:
    p, d = Path(path), pd.DataFrame()
    if p.exists() and p.stat().st_size > 0:
        try:
            d = pd.read_csv(p)
        except Exception:
            d = pd.DataFrame()
    return ensure_cols(d)


def save_csv(df: pd.DataFrame, path) -> None:
    ensure_cols(df)[COLUMNS].to_csv(path, index=False)


def model_sig(df: pd.DataFrame) -> int:
    if len(df) == 0:
        return 0
    return int(pd.util.hash_pandas_object(df[COLUMNS].astype(str), index=False).sum() % (2 ** 62))


def validate_rows(df: pd.DataFrame, literature: bool = True):
    """Return (cleaned_df, problems). Saving should be blocked while problems is non-empty."""
    d = ensure_cols(df)
    d = d[(d["drug"].str.strip() != "") | (d["vesicle"].str.strip() != "")].reset_index(drop=True)
    problems = []
    for i, r in d.iterrows():
        tag = f"row {i + 1} ({r['drug'] or '?'})"
        if not r["drug"].strip():
            problems.append(f"{tag}: drug name missing")
        if r["vesicle"] not in K.VESICLES:
            problems.append(f"{tag}: vesicle must be one of {', '.join(K.VESICLES)}")
        if r["ionization"] not in K.IONIZATION:
            problems.append(f"{tag}: ionization must be one of {', '.join(K.IONIZATION)}")
        for c, (lo, hi) in LIMITS.items():
            x = r[c]
            if pd.notna(x) and not (lo <= x <= hi):
                problems.append(f"{tag}: {c} = {x:g} is outside {lo:g} to {hi:g}")
        for c in ("mw", "logp", "solubility_mg_ml", "drug_to_lipid"):
            if pd.isna(r[c]):
                problems.append(f"{tag}: {c} is required")
        if r["vesicle"] in K.VESICLES:
            for c in K.DESIGN[r["vesicle"]]["free"]:
                if pd.isna(r[c]):
                    problems.append(f"{tag}: {c} is required for {r['vesicle']}")
        if not any(pd.notna(r[o]) for o in K.OUTPUTS):
            problems.append(f"{tag}: no measured value (size / PDI / zeta / EE)")
        if literature and not r["doi"].strip():
            problems.append(f"{tag}: DOI is required for literature rows")
    return d, problems


# ------------------------------------------------------------------ 2. carrier ranking
_OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b, "==": lambda a, b: a == b, "!=": lambda a, b: a != b}


def rank_carriers(drug: Drug, route: str, site: str, release: str) -> list:
    ctx = dict(logp=drug.logp, mw=drug.mw, pka=drug.pka, ionization=drug.ionization, solubility=drug.solubility,
               labile=drug.labile, phyto=drug.phyto, route=route, site=site, release=release)
    info = K.SITES[route][site]
    out = []
    for v in K.VESICLES:
        base = K.ROUTE_SCORE[route].get(v, 0.0)
        contribs = [dict(group="Route", label=route, points=base, why=K.ROUTE_WHY.get((route, v), ""))]
        sb = info["bonus"].get(v, 0.0)
        if sb:
            contribs.append(dict(group="Target site", label=site, points=sb, why=info["note"]))
        for r in K.RULES:
            pts = r["pts"].get(v, 0.0)
            if pts and all(_OPS[op](ctx[f], val) for f, op, val in r["when"]):
                contribs.append(dict(group=r["group"], label=r["label"], points=pts, why=r["why"]))
        out.append(dict(vesicle=v, score=float(sum(c["points"] for c in contribs)), suitable=base > 0,
                        why_not=K.UNSUITABLE.get((route, v), ""), contribs=contribs))
    out.sort(key=lambda r: (not r["suitable"], -r["score"], K.VESICLES.index(r["vesicle"])))
    return out


# ------------------------------------------------------------------ 3. design space + priors
def design_bounds(vesicle: str, drug: Drug, release: str) -> dict:
    spec = K.DESIGN[vesicle]
    if vesicle == "Phytosome":
        lo_r, hi_r = K.PHYTO_MOLAR_RANGE
        lo = round(drug.mw / (hi_r * K.MW["Soy PC"]), 2)
        hi = round(drug.mw / (lo_r * K.MW["Soy PC"]), 2)
        return {"drug_to_lipid": (lo, max(hi, lo + 0.05))}
    b = {c: tuple(spec["nominal"][c]) for c in spec["free"]}
    ov = K.RELEASE_RANGE.get((vesicle, release))
    if ov and "modifier_pct" in b:
        b["modifier_pct"] = ov
    return b


def _norm(vesicle: str, col: str, x) -> np.ndarray:
    x = np.asarray(x, float)
    lo, hi = K.DESIGN[vesicle]["nominal"][col]
    if col == "drug_to_lipid":
        return (np.log10(np.clip(x, 1e-6, None)) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))
    return (x - lo) / (hi - lo)


def _round_design(X: pd.DataFrame) -> pd.DataFrame:
    X = X.copy()
    for c in DESIGN_COLS:
        X[c] = np.round(X[c] / K.STEP[c]) * K.STEP[c]
    return X.round(4)


def _frame(drug: Drug, vesicle: str, X: pd.DataFrame) -> pd.DataFrame:
    d = X[DESIGN_COLS].copy().reset_index(drop=True)
    for k, v in drug.props().items():
        d[k] = v
    d["vesicle"] = vesicle
    return d


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    d = ensure_cols(df)
    d = d[d["vesicle"].isin(K.VESICLES)].copy()
    for v in K.VESICLES:
        m = d["vesicle"] == v
        for col, val in K.DESIGN[v]["fixed"].items():
            d.loc[m, col] = d.loc[m, col].fillna(val)
    d["ionization"] = d["ionization"].where(d["ionization"].isin(K.IONIZATION), "Neutral")
    d = d.dropna(subset=["mw", "logp", "solubility_mg_ml", "drug_to_lipid", "modifier_pct", "lipid_conc_pct"])
    return d.reset_index(drop=True)


def prior_frame(d: pd.DataFrame) -> pd.DataFrame:
    """Literature-typical expectation for every row (d must be _prep'd)."""
    out = pd.DataFrame(np.nan, index=d.index, columns=K.OUTPUTS)
    for v, g in d.groupby("vesicle"):
        m = g["modifier_pct"].to_numpy(float)
        m_n = _norm(v, "modifier_pct", m)
        c_n = _norm(v, "lipid_conc_pct", g["lipid_conc_pct"].to_numpy(float))
        dl = g["drug_to_lipid"].to_numpy(float)
        t = _norm(v, "drug_to_lipid", dl)
        logp, mw, pka = (g[c].to_numpy(float) for c in ("logp", "mw", "pka"))
        ion = g["ionization"].map(K.ZETA_ION).fillna(0.0).to_numpy(float)
        size = np.exp(math.log(K.SIZE_BASE[v]) + K.SIZE_SLOPE_DL * (t - 0.5)
                      + K.SIZE_SLOPE_MOD.get(v, 0.0) * (m_n - 0.5) + K.SIZE_SLOPE_CONC.get(v, 0.0) * (c_n - 0.5))
        pdi = np.clip(K.PDI_BASE[v] + K.PDI_SLOPE_DL * (t - 0.5) + K.PDI_SLOPE_MOD.get(v, 0.0) * (m_n - 0.5), 0.05, 0.9)
        zeta = K.ZETA_BASE[v] + ion + K.ZETA_MOD_SLOPE.get(v, 0.0) * (m_n - 0.5)
        if v == "Phytosome":
            r = mw / (np.clip(dl, 1e-3, None) * K.MW["Soy PC"])
            ee = K.EE_PHYTO - K.EE_PHYTO_CURVE * ((r - K.PHYTO_MOLAR_OPT) / 1.2) ** 2
        else:
            s = np.clip(logp / 3.0, 0, 1)
            ee0 = K.EE_HYDRO + (K.EE_LIPO - K.EE_HYDRO) * s + K.EE_OFFSET.get(v, 0.0)
            if v == "Liposome":
                remote = (g["ionization"].to_numpy() == "Weak base") & (pka >= 6.5)
                ee0 = np.where(remote, np.maximum(ee0, K.EE_REMOTE), ee0)
            lo, hi = K.DESIGN[v]["nominal"]["modifier_pct"]
            curve = K.EE_MOD_CURVE * ((m - K.EE_MOD_OPT[v]) / ((hi - lo) / 2.0)) ** 2 if v in K.EE_MOD_OPT else 0.0
            ee = ee0 + K.EE_DL_SLOPE * (t - 0.5) - curve + K.EE_CONC_SLOPE.get(v, 0.0) * (c_n - 0.5)
        out.loc[g.index, K.OUTPUTS] = np.column_stack([size, pdi, zeta, np.clip(ee, 1, 99)])
    return out


def prior_mean(drug: Drug, vesicle: str, X: pd.DataFrame) -> pd.DataFrame:
    return prior_frame(_prep(_frame(drug, vesicle, X)))


# ------------------------------------------------------------------ 4. surrogate (prior + GP correction)
W_V, W_D, W_X = 1.2, 1.0, 20.0       # feature-group weights: vesicle, design variables, drug descriptors
# W_X is large on purpose: other drugs barely influence a prediction (no evidence of transfer yet), so the model
# learns locally from rows of the SAME drug, plus a small shrunk per-vesicle bias correction.


def features(d: pd.DataFrame) -> np.ndarray:
    ves = np.column_stack([(d["vesicle"] == v).to_numpy(float) for v in K.VESICLES]) * W_V
    des = np.zeros((len(d), 3))
    for j, col in enumerate(DESIGN_COLS):
        for v in d["vesicle"].unique():
            m = (d["vesicle"] == v).to_numpy()
            des[m, j] = _norm(v, col, d.loc[m, col].to_numpy(float))
    des = np.clip(des, -0.5, 1.5) * W_D
    logp, mw, pka = (d[c].to_numpy(float) for c in ("logp", "mw", "pka"))
    sol = np.log10(np.clip(d["solubility_mg_ml"].to_numpy(float), 1e-6, None))
    drg = np.column_stack([(logp + 3) / 10, (np.log10(mw) - 2) / 1.7, np.where(np.isnan(pka), 0.5, pka / 14.0),
                           (sol + 6) / 18, (d["ionization"] == "Weak acid").to_numpy(float),
                           (d["ionization"] == "Weak base").to_numpy(float)]) * W_X
    return np.hstack([ves, des, drg])


def _fwd(o, x):
    return np.log(x) if o == "size_nm" else x


def _inv(o, x):
    return np.exp(x) if o == "size_nm" else x


class Surrogate:
    """prediction = literature prior + Gaussian-process correction learned from rows.
    With no rows it IS the prior (wide error bars). Amplitude of the correction is fixed to the prior
    uncertainty (design_knowledge.PRIOR_SD). The kernel amplitude, length-scale and observation-noise level are
    fixed to conservative prior-scaled values to avoid unstable hyperparameter fitting on tiny lab datasets;
    posterior mean/uncertainty still update as measured rows are added."""

    def __init__(self, df: pd.DataFrame | None = None):
        self.models, self.offset, self.n_rows, self.n_drugs = {}, {}, 0, 0
        if df is None or len(df) == 0:
            return
        d = _prep(df)
        self.n_rows, self.n_drugs = len(d), d["drug"].nunique()
        if not len(d):
            return
        F, P = features(d), prior_frame(d)
        for o in K.OUTPUTS:
            ok = d[o].notna().to_numpy()
            if ok.sum() < 3:
                continue
            y = _fwd(o, d.loc[ok, o].to_numpy(float)) - _fwd(o, P.loc[ok, o].to_numpy(float))
            ves_ok, drg_ok = d.loc[ok, "vesicle"].to_numpy(), d.loc[ok, "drug"].to_numpy()
            sd = max(K.PRIOR_SD_BY_VESICLE.get(str(v), K.PRIOR_SD)[o] for v in np.unique(ves_ok))
            for v in np.unique(ves_ok):      # bias calibration per vesicle; weight = number of DISTINCT drugs, shrunk towards 0
                mv = ves_ok == v
                per_drug = pd.Series(y[mv]).groupby(drg_ok[mv]).mean()
                self.offset[(o, v)] = float(per_drug.mean() * len(per_drug) / (len(per_drug) + 5.0))
                y[mv] = y[mv] - self.offset[(o, v)]
            kern = C(sd ** 2, "fixed") * Matern(1.0, "fixed", nu=2.5) + WhiteKernel((0.35 * sd) ** 2, "fixed")
            self.models[o] = GaussianProcessRegressor(kern, alpha=1e-8, n_restarts_optimizer=1, random_state=0).fit(F[ok], y)

    def predict_prepped(self, d: pd.DataFrame) -> pd.DataFrame:
        F, P = features(d), prior_frame(d)
        res = pd.DataFrame(index=d.index)
        for o in K.OUTPUTS:
            mu0 = _fwd(o, P[o].to_numpy(float))
            if o in self.models:
                r, s = self.models[o].predict(F, return_std=True)
            else:
                r, s = np.zeros(len(d)), np.array([K.PRIOR_SD_BY_VESICLE.get(v, K.PRIOR_SD)[o] for v in d["vesicle"]], dtype=float)
            off = np.array([self.offset.get((o, v), 0.0) for v in d["vesicle"]])
            res[o] = _inv(o, mu0 + off + r)
            res[o + "_sd"] = np.maximum(s, 1e-6)
        res["pdi"] = res["pdi"].clip(0.03, 1.0)
        res["ee_pct"] = res["ee_pct"].clip(0, 100)
        return res

    def predict(self, drug: Drug, vesicle: str, X: pd.DataFrame) -> pd.DataFrame:
        d = _prep(_frame(drug, vesicle, X))
        assert len(d) == len(X), "design frame lost rows"
        return self.predict_prepped(d)


# ------------------------------------------------------------------ 5. specs + scoring
def default_ee_min(drug: Drug, vesicle: str) -> float:
    if vesicle == "Phytosome" or (vesicle == "Liposome" and drug.ionization == "Weak base" and drug.pka >= 6.5):
        return 70.0
    return 70.0 if drug.logp >= 1.5 else (40.0 if drug.logp >= 0.5 else 25.0)


def default_specs(drug: Drug, route: str, site: str, vesicle: str) -> Specs:
    lo, hi = K.SITES[route][site]["size"]
    iv = route == "Intravenous"                      # PEG-stabilised: low zeta is acceptable
    return Specs(float(lo), float(hi), 0.20 if iv else 0.30, 5.0 if iv else 20.0, default_ee_min(drug, vesicle))


def _ramp_hi(x, bound, tol):
    return np.clip(1.0 - np.maximum(x - bound, 0.0) / tol, 0.0, 1.0)


def _ramp_lo(x, bound, tol):
    return np.clip(1.0 - np.maximum(bound - x, 0.0) / tol, 0.0, 1.0)


def desirability(o: dict, s: Specs):
    """Weighted geometric mean of per-property desirabilities (1 inside spec, ramps to 0 outside)."""
    d = {"size_nm": _ramp_hi(o["size_nm"], s.size_hi, 0.5 * s.size_hi),
         "pdi": _ramp_hi(o["pdi"], s.pdi_max, 0.2),
         "zeta_mv": _ramp_lo(np.abs(o["zeta_mv"]), s.zeta_min, max(0.75 * s.zeta_min, 5.0)),
         "ee_pct": _ramp_lo(o["ee_pct"], s.ee_min, 0.6 * s.ee_min)}
    if s.size_lo > 0:
        d["size_nm"] = d["size_nm"] * _ramp_lo(o["size_nm"], s.size_lo, 0.5 * s.size_lo)
    num = sum(K.WEIGHTS[k] * np.log(np.clip(d[k], 1e-3, 1.0)) for k in K.OUTPUTS)
    return np.exp(num / sum(K.WEIGHTS.values()))


def passes(o: dict, s: Specs):
    return ((o["size_nm"] >= s.size_lo) & (o["size_nm"] <= s.size_hi) & (o["pdi"] <= s.pdi_max)
            & (np.abs(o["zeta_mv"]) >= s.zeta_min) & (o["ee_pct"] >= s.ee_min))


def meets(row, s: Specs) -> dict:
    return {"size": bool(s.size_lo <= row["size_nm"] <= s.size_hi), "PDI": bool(row["pdi"] <= s.pdi_max),
            "zeta": bool(abs(row["zeta_mv"]) >= s.zeta_min), "EE": bool(row["ee_pct"] >= s.ee_min)}


def score(pred: pd.DataFrame, s: Specs, S: int = 64, seed: int = 0) -> dict:
    """Monte-Carlo over the predictive distribution -> E[desirability], its sd, P(all specs met)."""
    rng, m = np.random.default_rng(seed), len(pred)
    z = rng.standard_normal((4, S, m))
    v = lambda c: pred[c].to_numpy(float)[None, :]
    smp = {"size_nm": v("size_nm") * np.exp(v("size_nm_sd") * z[0]),
           "pdi": np.clip(v("pdi") + v("pdi_sd") * z[1], 0.02, 1.0),
           "zeta_mv": v("zeta_mv") + v("zeta_mv_sd") * z[2],
           "ee_pct": np.clip(v("ee_pct") + v("ee_pct_sd") * z[3], 0, 100)}
    D = desirability(smp, s)
    return {"D": D.mean(0), "Dsd": D.std(0), "P": passes(smp, s).mean(0)}


def d_from_rows(df: pd.DataFrame, s: Specs) -> np.ndarray:
    return desirability({o: df[o].to_numpy(float) for o in K.OUTPUTS}, s)


# ------------------------------------------------------------------ 6. optimiser
def kappa_for(n_own: int) -> float:
    """Explore early (large kappa), exploit later."""
    return max(0.3, 1.5 - 0.2 * n_own)


def candidates(vesicle: str, drug: Drug, release: str, n: int, rng) -> tuple:
    b = design_bounds(vesicle, drug, release)
    free = list(b)
    lo = np.array([b[c][0] for c in free], float)
    hi = np.array([b[c][1] for c in free], float)
    X = pd.DataFrame(lo + rng.random((n, len(free))) * (hi - lo), columns=free)
    for c in DESIGN_COLS:
        if c not in X:
            X[c] = K.DESIGN[vesicle]["fixed"][c]
    return _round_design(X[DESIGN_COLS]).drop_duplicates().reset_index(drop=True), free, lo, hi


def random_design(vesicle: str, drug: Drug, release: str, n: int, rng) -> pd.DataFrame:
    return candidates(vesicle, drug, release, max(n * 4, 40), rng)[0].head(n).reset_index(drop=True)


def propose(model: Surrogate, drug: Drug, vesicle: str, specs: Specs, release: str = "Sustained",
            n: int = 5, kappa: float = 1.0, seed: int = 0, n_cand: int = 2048, acq: str = "ucb") -> pd.DataFrame:
    """Batch Bayesian optimisation: rank candidates by E[D] + kappa*sd[D], pick n spread-out batches."""
    X, free, lo, hi = candidates(vesicle, drug, release, n_cand, np.random.default_rng(seed))
    pred = model.predict(drug, vesicle, X)
    sc = score(pred, specs, seed=seed)
    if acq == "pass":        # probability of meeting every spec (tie-break on D)
        ucb = sc["P"] + 0.25 * sc["D"]
    elif acq == "hybrid":
        ucb = sc["D"] + sc["P"] + kappa * sc["Dsd"]
    else:                    # "ucb": expected desirability + exploration bonus
        ucb = sc["D"] + kappa * sc["Dsd"]
    Z = (X[free].to_numpy(float) - lo) / np.maximum(hi - lo, 1e-9)
    r = 0.6 * (1.0 / n) ** (1.0 / len(free))             # minimum spacing between chosen batches
    chosen, dmin = [], np.full(len(X), np.inf)
    while len(chosen) < min(n, len(X)):
        ok = dmin >= r
        ok[chosen] = False
        if not ok.any():
            r *= 0.5
            if r < 1e-3:
                break
            continue
        j = int(np.argmax(np.where(ok, ucb, -np.inf)))
        chosen.append(j)
        dmin = np.minimum(dmin, np.linalg.norm(Z - Z[j], axis=1))
    out = pd.concat([X.iloc[chosen].reset_index(drop=True), pred.iloc[chosen].reset_index(drop=True)], axis=1)
    out["desirability"], out["p_success"] = sc["D"][chosen], sc["P"][chosen]
    out.insert(0, "batch", range(1, len(out) + 1))
    return out


# ------------------------------------------------------------------ 7. extras
def make_rows(drug: Drug, vesicle: str, X: pd.DataFrame, outs, source: str = "lab", rnd: int = 1, notes="") -> pd.DataFrame:
    d = _frame(drug, vesicle, X)
    for o in K.OUTPUTS:
        d[o] = np.asarray(outs[o], float)
    d["source"], d["doi"], d["verified"], d["round"] = source, "", True, rnd
    d["notes"] = notes if isinstance(notes, str) else list(notes)
    return ensure_cols(d)[COLUMNS]


_AMP = {"size_nm": 0.35, "pdi": 0.09, "zeta_mv": 12.0, "ee_pct": 20.0}
_NOISE = {"size_nm": 0.05, "pdi": 0.02, "zeta_mv": 2.0, "ee_pct": 2.5}


def virtual_lab(drug: Drug, vesicle: str, X: pd.DataFrame, seed: int = 0, noise: bool = True) -> pd.DataFrame:
    """DEMO ONLY. A hidden smooth response surface = prior + deterministic perturbation (+ noise).
    Used to show the loop converging and to benchmark the optimiser. It says NOTHING about real chemistry."""
    X = X.reset_index(drop=True)
    base = prior_mean(drug, vesicle, X)
    U = np.column_stack([_norm(vesicle, c, X[c].to_numpy(float)) for c in DESIGN_COLS])
    rng = np.random.default_rng(zlib.crc32(f"{drug.name}|{vesicle}".encode()))
    out = pd.DataFrame(index=X.index)
    for o in K.OUTPUTS:
        g = np.sin(U @ rng.normal(0, 2.5, 3) + rng.uniform(0, 2 * np.pi))
        out[o] = base[o] * np.exp(_AMP[o] * g) if o == "size_nm" else base[o] + _AMP[o] * g
    if noise:
        r2 = np.random.default_rng(seed)
        out["size_nm"] = out["size_nm"] * np.exp(_NOISE["size_nm"] * r2.standard_normal(len(out)))
        for o in ("pdi", "zeta_mv", "ee_pct"):
            out[o] = out[o] + _NOISE[o] * r2.standard_normal(len(out))
    out["pdi"], out["ee_pct"] = out["pdi"].clip(0.03, 1.0), out["ee_pct"].clip(0, 100)
    return out


def lodo(df: pd.DataFrame, min_rows: int = 20, min_drugs: int = 5):
    """Leave-one-drug-out validation. Returns None when there is not enough REAL data to be meaningful."""
    d = _prep(df)
    if len(d) < min_rows or d["drug"].nunique() < min_drugs:
        return None
    errs = {o: {"mean": [], "prior": [], "model": []} for o in K.OUTPUTS}
    for g in d["drug"].unique():
        tr, te = d[d["drug"] != g], d[d["drug"] == g].reset_index(drop=True)
        pr, p0 = Surrogate(tr).predict_prepped(te), prior_frame(te)
        for o in K.OUTPUTS:
            ok = te[o].notna().to_numpy()
            base = tr[o].dropna()
            if not ok.any() or base.empty:
                continue
            y = te.loc[ok, o].to_numpy(float)
            errs[o]["mean"] += list(np.abs(y - base.mean()))
            errs[o]["prior"] += list(np.abs(y - p0.loc[ok, o].to_numpy(float)))
            errs[o]["model"] += list(np.abs(y - pr.loc[ok, o].to_numpy(float)))
    rows = [{"Property": K.OUT_LABEL[o], "Rows": len(e["model"]), "MAE: training mean": float(np.mean(e["mean"])),
             "MAE: literature prior": float(np.mean(e["prior"])), "MAE: prior + learning": float(np.mean(e["model"]))}
            for o, e in errs.items() if len(e["model"]) >= 8]
    return pd.DataFrame(rows) if rows else None


def precedents(df: pd.DataFrame, drug: Drug, k: int = 5) -> pd.DataFrame:
    d = _prep(df) if len(df) else df
    if len(d) == 0:
        return pd.DataFrame()
    g = d.sort_values("ee_pct", ascending=False).groupby("drug", sort=False).head(1).copy()
    sol = np.log10(np.clip(g["solubility_mg_ml"].to_numpy(float), 1e-6, None))
    g["distance"] = np.sqrt(((g["logp"].to_numpy(float) - drug.logp) / 2.0) ** 2
                            + ((np.log10(g["mw"].to_numpy(float)) - math.log10(drug.mw)) / 0.3) ** 2
                            + ((sol - math.log10(max(drug.solubility, 1e-6))) / 1.5) ** 2
                            + (g["ionization"].to_numpy() != drug.ionization) * 0.25)
    return g.nsmallest(k, "distance")[["drug", "distance", "vesicle", "size_nm", "ee_pct", "doi"]].reset_index(drop=True)


def recipe(vesicle: str, drug: Drug, row, size: float, release: str = "Sustained", route: str = "Oral"):
    """Bench amounts for one batch. size = mg total lipid (Liposome/Niosome/Transfersome),
    mL final volume (Ethosome) or mg drug (Phytosome). Returns (table, footnote)."""
    m, c, dl = float(row["modifier_pct"]), float(row["lipid_conc_pct"]), float(row["drug_to_lipid"])
    rows, foot = [], ""

    def add(name, amount, note=""):
        rows.append(dict(Component=name, Amount=amount, Note=note))

    if vesicle in ("Liposome", "Niosome"):
        if vesicle == "Liposome":
            main = "HSPC" if (release != "Immediate" or route == "Intravenous") else "Soy PC"
            peg = 5.0 if route == "Intravenous" else 0.0
            comps = [(main, 100 - m - peg), ("Cholesterol", m)] + ([("DSPE-mPEG2000", peg)] if peg else [])
        else:
            comps = [("Span 60", 100 - m), ("Cholesterol", m)]
        wts = [x * K.MW[n] for n, x in comps]
        for (n, x), w in zip(comps, wts):
            add(n, f"{size * w / sum(wts):.1f} mg", f"{x:.1f} mol %")
        add(drug.name, f"{size * dl:.1f} mg", f"drug : lipid = {dl:.2f} w/w")
        foot = f"Hydrate in about {size / 10:.0f} mL buffer (10 mg total lipid/mL) above the lipid Tm."
    elif vesicle == "Transfersome":
        ea = size * m / 100
        add("Soy PC", f"{size - ea:.1f} mg", f"{100 - m:.0f} % w/w")
        add("Edge activator (Tween 80)", f"{ea:.1f} mg", f"{m:.0f} % w/w; also screen Span 80 / sodium cholate")
        add(drug.name, f"{size * dl:.1f} mg", f"drug : lipid = {dl:.2f} w/w")
        foot = f"Hydrate in about {size / 50:.0f} mL buffer (50 mg lipid/mL), then sonicate / extrude."
    elif vesicle == "Ethosome":
        pc = c * 10 * size                                    # 1 % w/v = 10 mg/mL
        add("Soy PC", f"{pc:.1f} mg", f"{c:.1f} % w/v")
        add(drug.name, f"{pc * dl:.1f} mg", f"drug : lipid = {dl:.2f} w/w")
        add("Ethanol", f"{m / 100 * size:.1f} mL", f"{m:.0f} % v/v")
        add("Water / buffer", f"to {size:.0f} mL", "add slowly while stirring")
        foot = f"Final volume {size:.0f} mL."
    else:
        pc = size / dl
        add(drug.name, f"{size:.1f} mg", "")
        add("Soy PC", f"{pc:.1f} mg", f"PC : drug = {drug.mw / (dl * K.MW['Soy PC']):.2f} mol/mol")
        foot = "React in an aprotic solvent (about 20 mL per g of PC), evaporate, then hydrate."
    return pd.DataFrame(rows), foot


def plan(vesicle: str, drug: Drug, route: str, site: str, release: str, bounds: dict) -> dict:
    comps, notes = [], []
    m = bounds.get("modifier_pct", (0, 0))
    if vesicle == "Liposome":
        rigid = release != "Immediate" or route == "Intravenous"
        comps.append(("Bilayer lipid", "HSPC or DSPC (Tm about 51-55 C: rigid, low leakage)" if rigid else "Soy or egg PC (fluid bilayer, faster release)"))
        comps.append(("Sterol", f"Cholesterol, {m[0]:.0f}-{m[1]:.0f} mol %"))
        if route == "Intravenous":
            comps.append(("Stealth coating", "DSPE-mPEG2000, about 5 mol % (long circulation)"))
        if release.startswith("Triggered"):
            comps.append(("Trigger lipid", "pH-sensitive: DOPE : CHEMS about 6 : 4 mol; thermosensitive: DPPC-based + lysolipid or PEG-lipid"))
        comps.append(("Charge lipid (optional)", "DCP or DSPG, 5-10 mol %, if |zeta| is too low and the vesicle is not PEGylated"))
        if drug.ionization == "Weak base" and drug.pka >= 6.5:
            comps.append(("Loading", "Remote loading with an ammonium-sulfate gradient (EE typically > 90 %)"))
        elif drug.logp < 1:
            comps.append(("Loading", "Passive loading in the aqueous core: expect low EE; consider freeze-thaw cycles or reverse-phase evaporation"))
        else:
            comps.append(("Loading", "Passive: add the lipophilic drug to the lipid film"))
    elif vesicle == "Niosome":
        comps += [("Surfactant", "Span 60 (default); Tween 60 or Span 40 as alternatives"),
                  ("Sterol", f"Cholesterol, {m[0]:.0f}-{m[1]:.0f} mol %"),
                  ("Charge lipid (optional)", "DCP 5 mol % (negative) or stearylamine (positive, ocular residence)")]
    elif vesicle == "Transfersome":
        comps += [("Bilayer lipid", "Soy phosphatidylcholine"),
                  ("Edge activator", f"Tween 80 (default), Span 80 or sodium cholate: {m[0]:.0f}-{m[1]:.0f} % w/w of lipid"),
                  ("Medium", "Phosphate buffer, pH 6.5-7.4")]
    elif vesicle == "Ethosome":
        c = bounds["lipid_conc_pct"]
        comps += [("Phospholipid", f"Soy PC, {c[0]:.1f}-{c[1]:.1f} % w/v"), ("Ethanol", f"{m[0]:.0f}-{m[1]:.0f} % v/v"),
                  ("Optional", "Propylene glycol can partly replace ethanol (binary ethosomes)")]
    else:
        b = bounds["drug_to_lipid"]
        comps += [("Phospholipid", "Soy PC with >= 70-90 % phosphatidylcholine"),
                  ("Complex ratio", f"PC : drug = {K.PHYTO_MOLAR_RANGE[0]:.1f}-{K.PHYTO_MOLAR_RANGE[1]:.1f} mol/mol (about {1 / b[1]:.1f}-{1 / b[0]:.1f} w/w)")]
    notes.append(K.SITES[route][site]["note"])
    notes += K.INFO[vesicle]["tips"]
    if release == "Sustained" and vesicle in ("Liposome", "Niosome"):
        notes.append("Sustained release: the search is centred on the high-cholesterol end (rigid bilayer, slower leakage).")
    if release == "Immediate" and vesicle in ("Liposome", "Niosome"):
        notes.append("Immediate release: the search is centred on the low-cholesterol end with fluid lipids.")
    if drug.labile:
        notes.append("Labile drug: add alpha-tocopherol (0.5-1 mol %), protect from light, purge with N2; lyophilise with a cryoprotectant (e.g. trehalose) for storage.")
    if drug.logp < 1 and vesicle in ("Transfersome", "Ethosome", "Niosome"):
        notes.append("Hydrophilic drug: expect modest EE with passive loading; optimise lipid concentration first.")
    if vesicle == "Phytosome" and not drug.phyto:
        notes.append("This compound is not flagged as a polar phytoconstituent: complexation with PC may be weak. Confirm by FTIR/DSC.")
    return dict(components=comps, method=K.INFO[vesicle]["method"], notes=notes, what=K.INFO[vesicle]["what"])


# ------------------------------------------------------------------ 8. persistent, real laboratory feedback (SmartNano integration)
LAB_TABLE = "design_lab_runs"


def ensure_lab_table(con: sqlite3.Connection) -> None:
    """Create the additive lab-loop table; never writes synthetic/virtual outcomes."""
    con.execute(f"""CREATE TABLE IF NOT EXISTS {LAB_TABLE} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        drug TEXT NOT NULL,
        mw REAL NOT NULL,
        logp REAL NOT NULL,
        pka REAL,
        ionization TEXT NOT NULL,
        solubility_mg_ml REAL NOT NULL,
        labile INTEGER NOT NULL DEFAULT 0,
        phyto INTEGER NOT NULL DEFAULT 0,
        route TEXT NOT NULL,
        site TEXT NOT NULL,
        release_profile TEXT NOT NULL,
        vesicle TEXT NOT NULL,
        modifier_pct REAL,
        lipid_conc_pct REAL,
        drug_to_lipid REAL NOT NULL,
        size_nm REAL,
        pdi REAL,
        zeta_mv REAL,
        ee_pct REAL,
        round_no INTEGER NOT NULL DEFAULT 1,
        target_organ TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'user_lab',
        verified INTEGER NOT NULL DEFAULT 1
    )""")
    # Additive migration if an earlier development table already exists.
    existing = {r[1] for r in con.execute(f"PRAGMA table_info({LAB_TABLE})").fetchall()}
    if "target_organ" not in existing:
        con.execute(f"ALTER TABLE {LAB_TABLE} ADD COLUMN target_organ TEXT NOT NULL DEFAULT ''")
    con.execute(f"CREATE INDEX IF NOT EXISTS idx_{LAB_TABLE}_drug ON {LAB_TABLE}(drug, vesicle)")
    con.commit()


def lab_frame(con: sqlite3.Connection) -> pd.DataFrame:
    ensure_lab_table(con)
    rows = con.execute(f"""SELECT drug,mw,logp,pka,ionization,solubility_mg_ml,vesicle,
        modifier_pct,lipid_conc_pct,drug_to_lipid,size_nm,pdi,zeta_mv,ee_pct,
        source, '' AS doi, verified, round_no AS round, notes
        FROM {LAB_TABLE} ORDER BY id""").fetchall()
    if not rows:
        return empty_rows()
    cols = ["drug","mw","logp","pka","ionization","solubility_mg_ml","vesicle",
            "modifier_pct","lipid_conc_pct","drug_to_lipid","size_nm","pdi","zeta_mv","ee_pct",
            "source","doi","verified","round","notes"]
    return ensure_cols(pd.DataFrame([tuple(r) for r in rows], columns=cols))


def lab_summary(con: sqlite3.Connection, drug: str | None = None, vesicle: str | None = None) -> dict:
    ensure_lab_table(con)
    where, args = [], []
    if drug:
        where.append("drug = ?"); args.append(drug)
    if vesicle:
        where.append("vesicle = ?"); args.append(vesicle)
    sql = f"SELECT COUNT(*) n, COUNT(DISTINCT drug) drugs, MAX(round_no) rounds FROM {LAB_TABLE}"
    if where: sql += " WHERE " + " AND ".join(where)
    r = con.execute(sql, args).fetchone()
    outputs = {}
    for col in K.OUTPUTS:
        q = f"SELECT COUNT({col}) FROM {LAB_TABLE}" + (" WHERE " + " AND ".join(where) if where else "")
        outputs[col] = int(con.execute(q, args).fetchone()[0])
    return {"rows": int(r[0]), "drugs": int(r[1]), "rounds": int(r[2] or 0), "labelled_outputs": outputs}


def save_lab_results(con: sqlite3.Connection, payload: dict) -> dict:
    """Validate and save actual measured results only. Each proposal is one row."""
    ensure_lab_table(con)
    try:
        drug = Drug(
            name=str(payload.get("drug") or "").strip(),
            mw=float(payload.get("mw")), logp=float(payload.get("logp")),
            ionization=str(payload.get("ionization") or "Neutral"),
            pka=float(payload["pka"]) if payload.get("pka") not in (None, "") else NAN,
            solubility=float(payload.get("solubility_mg_ml")),
            labile=bool(payload.get("labile")), phyto=bool(payload.get("phyto")))
    except (TypeError, ValueError):
        raise ValueError("Enter valid drug name, molecular weight, LogP and aqueous solubility before saving results.")
    if not drug.name or not (50 <= drug.mw <= 5000) or not (-8 <= drug.logp <= 12):
        raise ValueError("Drug name, molecular weight (50–5000) and LogP (−8 to 12) are required.")
    if not (1e-6 <= drug.solubility <= 2000):
        raise ValueError("Aqueous solubility must be between 0.000001 and 2000 mg/mL.")
    vesicle = str(payload.get("vesicle") or "")
    if vesicle not in K.VESICLES:
        raise ValueError("Choose a supported nanovesicle type.")
    route, site = str(payload.get("route") or ""), str(payload.get("site") or "")
    release = str(payload.get("release") or "Sustained")
    if route not in K.SITES or site not in K.SITES[route]:
        raise ValueError("Choose a valid route and target site combination.")
    if release not in K.RELEASES:
        raise ValueError("Choose a supported release profile.")
    measured = payload.get("measured") or []
    if not isinstance(measured, list) or not measured:
        raise ValueError("Enter at least one measured batch result.")
    round_no = int(payload.get("round") or 1)
    if round_no < 1 or round_no > 1000:
        raise ValueError("Lab round must be between 1 and 1000.")
    saved, errors = [], []
    con.execute("BEGIN")
    try:
        for i, item in enumerate(measured, 1):
            if not isinstance(item, dict):
                errors.append(f"Batch {i}: malformed result."); continue
            try:
                x = {c: float(item[c]) if item.get(c) not in (None, "") else NAN for c in DESIGN_COLS}
                # Fixed variables are filled consistently with carrier definitions.
                for c, v in K.DESIGN[vesicle]["fixed"].items():
                    if not math.isfinite(x[c]): x[c] = float(v)
                outs = {c: float(item[c]) if item.get(c) not in (None, "") else NAN for c in K.OUTPUTS}
                if not any(math.isfinite(v) for v in outs.values()):
                    raise ValueError("at least one measured output (size, PDI, zeta or EE) is required")
                row = {**drug.props(), "vesicle": vesicle, **x, **outs}
                d, problems = validate_rows(pd.DataFrame([row]), literature=False)
                if problems:
                    raise ValueError("; ".join(problems))
                rid = str(uuid4())
                created = datetime.now(timezone.utc).isoformat(timespec="seconds")
                notes = str(item.get("notes") or payload.get("notes") or "").strip()[:2000]
                con.execute(f"""INSERT INTO {LAB_TABLE} (
                    run_id,created_at,drug,mw,logp,pka,ionization,solubility_mg_ml,labile,phyto,
                    route,site,target_organ,release_profile,vesicle,modifier_pct,lipid_conc_pct,drug_to_lipid,
                    size_nm,pdi,zeta_mv,ee_pct,round_no,notes,source,verified)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (rid,created,drug.name,drug.mw,drug.logp,None if not math.isfinite(drug.pka) else drug.pka,
                     drug.ionization,drug.solubility,int(drug.labile),int(drug.phyto),route,site,str(payload.get("organ") or ""),release,vesicle,
                     None if not math.isfinite(x["modifier_pct"]) else x["modifier_pct"],
                     None if not math.isfinite(x["lipid_conc_pct"]) else x["lipid_conc_pct"],x["drug_to_lipid"],
                     *[None if not math.isfinite(outs[c]) else outs[c] for c in K.OUTPUTS],round_no,notes,"user_lab",1))
                saved.append(rid)
            except Exception as e:
                errors.append(f"Batch {i}: {e}")
        if errors:
            raise ValueError("No results saved because one or more rows failed validation: " + " | ".join(errors))
        con.commit()
    except Exception:
        con.rollback()
        raise
    return {"saved": len(saved), "run_ids": saved, "summary": lab_summary(con, drug.name, vesicle),
            "message": "Measured lab results saved. They will inform the next proposal for this design space."}


def make_recommendation(con: sqlite3.Connection, payload: dict) -> dict:
    """Transparent heuristic carrier ranking + prior/GP Bayesian design proposals."""
    name = str(payload.get("drug") or payload.get("name") or "").strip()
    if not name:
        raise ValueError("Enter a drug or active-ingredient name.")
    def num(key, required=False, default=NAN):
        value = payload.get(key)
        if value in (None, ""):
            if required: raise ValueError(f"{key} is required for optimisation.")
            return default
        try: return float(value)
        except (TypeError, ValueError): raise ValueError(f"{key} must be numeric.")
    drug = Drug(name=name, mw=num("mw", True), logp=num("logp", True),
                ionization=str(payload.get("ionization") or "Neutral"), pka=num("pka"),
                solubility=num("solubility_mg_ml", True), labile=bool(payload.get("labile")),
                phyto=bool(payload.get("phyto")))
    if not (50 <= drug.mw <= 5000 and -8 <= drug.logp <= 12 and 1e-6 <= drug.solubility <= 2000):
        raise ValueError("Check MW (50–5000), LogP (−8 to 12) and solubility (0.000001–2000 mg/mL).")
    if drug.ionization not in K.IONIZATION: raise ValueError("Choose Neutral, Weak acid or Weak base ionisation.")
    if math.isfinite(drug.pka) and not (0 <= drug.pka <= 14): raise ValueError("pKa must be between 0 and 14, or left blank.")
    route, site = str(payload.get("route") or "Oral"), str(payload.get("site") or "Systemic (absorption)")
    release = str(payload.get("release") or "Sustained")
    if route not in K.SITES or site not in K.SITES[route]:
        raise ValueError("Target site does not match the selected administration route.")
    if release not in K.RELEASES: raise ValueError("Choose Immediate, Sustained or Triggered (pH / temperature).")
    rankings = rank_carriers(drug, route, site, release)
    chosen = str(payload.get("vesicle") or "Auto")
    if chosen == "Auto":
        chosen = next((r["vesicle"] for r in rankings if r["suitable"]), rankings[0]["vesicle"])
    if chosen not in K.VESICLES: raise ValueError("Choose Auto or one of the supported nanovesicle classes.")
    bounds = design_bounds(chosen, drug, release)
    specs = default_specs(drug, route, site, chosen)
    all_rows = lab_frame(con)
    own_rows = all_rows[(all_rows["drug"].str.casefold() == drug.name.casefold()) & (all_rows["vesicle"] == chosen)]
    # Strictly local learning: train the active design loop only on this drug + carrier.
    # Cross-drug transfer is not assumed; LODO is reported separately as a diagnostic.
    model = Surrogate(own_rows)
    n = max(1, min(int(payload.get("batch_count") or 5), 8))
    seed = int(payload.get("seed") or 0)
    kappa = kappa_for(len(own_rows))
    proposals = propose(model, drug, chosen, specs, release=release, n=n, kappa=kappa,
                        seed=seed, n_cand=1024, acq="ucb")
    # One generic bench scale is made explicit; user should rescale using lab protocol.
    scale = num("batch_scale", default=100.0)
    if not (1 <= scale <= 10000): raise ValueError("Batch scale must be between 1 and 10000.")
    rec_row = proposals.iloc[0]
    recipe_df, recipe_note = recipe(chosen, drug, rec_row, scale, release=release, route=route)
    plan_data = plan(chosen, drug, route, site, release, bounds)
    records = []
    for _, r in proposals.iterrows():
        rec = {k: (float(v) if pd.notna(v) and np.isfinite(float(v)) else None)
               for k, v in r.to_dict().items()}
        # 95% uncertainty intervals: size sigma is on log scale; other outputs are additive.
        size_mu, size_log_sd = float(r["size_nm"]), float(r["size_nm_sd"])
        rec["size_nm_low95"] = size_mu * math.exp(-1.96 * size_log_sd)
        rec["size_nm_high95"] = size_mu * math.exp(1.96 * size_log_sd)
        for col, sd_col, lower, upper in (("pdi","pdi_sd",0.02,1.0),("zeta_mv","zeta_mv_sd",-100,100),("ee_pct","ee_pct_sd",0,100)):
            rec[col + "_low95"] = max(lower, float(r[col]) - 1.96 * float(r[sd_col]))
            rec[col + "_high95"] = min(upper, float(r[col]) + 1.96 * float(r[sd_col]))
        records.append(rec)
    drug_payload = drug.props()
    if not math.isfinite(drug.pka): drug_payload["pka"] = None
    lodo_result = lodo(all_rows) if len(all_rows) >= 20 and all_rows["drug"].nunique() >= 5 else None
    validation = ({"available": True, "metrics": lodo_result.to_dict(orient="records"),
                   "method": "Leave-one-drug-out; internal retrospective validation only."}
                  if lodo_result is not None else
                  {"available": False, "metrics": [],
                   "reason": f"Requires at least 20 measured rows across 5 distinct drugs; current data: {len(all_rows)} rows across {all_rows['drug'].nunique() if len(all_rows) else 0} drugs."})
    return {
        "drug": drug_payload, "route": route, "site": site, "organ": str(payload.get("organ") or ""),
        "release": release, "release_goal": str(payload.get("release_goal") or ""), "vesicle": chosen,
        "carrier_rankings": rankings, "design_bounds": {k:list(v) for k,v in bounds.items()},
        "specs": {"size_lo_nm":specs.size_lo,"size_hi_nm":specs.size_hi,"pdi_max":specs.pdi_max,
                  "zeta_min_abs_mv":specs.zeta_min,"ee_min_pct":specs.ee_min},
        "model": {"mode":"prior_plus_gp_lab_learning" if model.models else "prior_only",
                  "total_lab_rows":len(all_rows),"same_drug_carrier_rows":len(own_rows),
                  "trained_outputs":[K.OUT_LABEL[o] for o in model.models], "minimum_labels_per_output":3,
                  "uncertainty":"Wide prior uncertainty; estimates are hypotheses until measured.",
                  "kappa":kappa,"message":("The GP residual correction is fitted only to measured rows for this same drug and carrier. "
                  "The wider literature database is used for precedents, not treated as clean numeric labels." +
                  (" Outputs not listed as trained remain prior-only." if len(model.models) < len(K.OUTPUTS) else ""))},
        "proposals":records, "validation": validation,
        "recipe":{"components":recipe_df.to_dict(orient="records"),"note":recipe_note,"scale":scale,"scale_unit":"mL final volume" if chosen=="Ethosome" else ("mg drug" if chosen=="Phytosome" else "mg total lipid")},
        "plan":plan_data,
        "lab_summary":lab_summary(con, drug.name, chosen)
    }
