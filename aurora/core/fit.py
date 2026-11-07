"""Fits of John Bussey's tabs 4-6: linear combination fitting, PCA, MCR-ALS, ITFA. numpy / scipy only.

All spectra are interpolated onto the common energy grid of the overlap before fitting; the caller passes
(E, y) pairs already normalised and shifted by the Compare perspective.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize, nnls


def common_grid(curves: list[tuple[np.ndarray, np.ndarray]], fit_range: tuple[float, float] | None = None) -> np.ndarray:
    """Energies of the first curve inside the overlap of all curves (and the fit range)."""
    lo = max(float(np.nanmin(E)) for E, _ in curves)
    hi = min(float(np.nanmax(E)) for E, _ in curves)
    if fit_range:
        lo, hi = max(lo, fit_range[0]), min(hi, fit_range[1])
    E0 = curves[0][0]
    grid = E0[(E0 >= lo) & (E0 <= hi)]
    if grid.size < 3:
        raise ValueError("no overlapping energy range between the spectra")
    return grid


def on_grid(E: np.ndarray, y: np.ndarray, grid: np.ndarray) -> np.ndarray:
    fin = np.isfinite(y) & np.isfinite(E)
    return np.interp(grid, E[fin], y[fin]) if fin.sum() >= 2 else np.full(len(grid), np.nan)


# ----------------------------------------------------------------------------- LCF
@dataclass
class LcfResult:
    coefficients: np.ndarray
    uncertainties: np.ndarray
    E: np.ndarray
    target: np.ndarray
    fit: np.ndarray
    components: np.ndarray            # (n_comp, n_E)
    rmse: float
    r_factor: float
    n_points: int

    @property
    def total(self) -> float:
        return float(self.coefficients.sum())


def lcf(target: tuple[np.ndarray, np.ndarray], components: list[tuple[np.ndarray, np.ndarray]],
        fit_range: tuple[float, float] | None = None, sum_to_one: bool = True, allow_negative: bool = False,
        bootstrap: int = 200, seed: int = 0) -> LcfResult:
    """Least squares of the target on the components; coefficients bounded >= 0 unless allowed negative, optionally
    constrained to sum to 1 (SLSQP). Uncertainties: standard deviation over `bootstrap` residual-resampled refits."""
    grid = common_grid([target] + components, fit_range)
    t = on_grid(*target, grid)
    A = np.column_stack([on_grid(E, y, grid) for E, y in components])
    ok = np.isfinite(t) & np.all(np.isfinite(A), axis=1)
    t, A, grid = t[ok], A[ok], grid[ok]
    n = A.shape[1]

    def solve(tt):
        if not sum_to_one and not allow_negative:
            c, _ = nnls(A, tt)
            return c
        if not sum_to_one and allow_negative:
            return np.linalg.lstsq(A, tt, rcond=None)[0]
        x0 = np.full(n, 1.0 / n)
        cons = [{"type": "eq", "fun": lambda c: c.sum() - 1.0}]
        bounds = None if allow_negative else [(0.0, None)] * n
        r = minimize(lambda c: float(((A @ c - tt) ** 2).sum()), x0, bounds=bounds, constraints=cons, method="SLSQP",
                     options={"maxiter": 500, "ftol": 1e-12})
        return r.x

    c = solve(t)
    fit = A @ c
    resid = t - fit
    rmse = float(np.sqrt(np.mean(resid ** 2)))
    r_factor = float((resid ** 2).sum() / max((t ** 2).sum(), 1e-300))
    unc = np.full(n, np.nan)
    if bootstrap and len(t) > n + 2:
        rng = np.random.default_rng(seed)
        draws = np.array([solve(fit + rng.choice(resid, size=len(resid), replace=True)) for _ in range(bootstrap)])
        unc = draws.std(axis=0)
    return LcfResult(c, unc, grid, t, fit, A.T, rmse, r_factor, int(len(t)))


# ----------------------------------------------------------------------------- PCA
@dataclass
class PcaResult:
    explained: np.ndarray             # fraction per component
    components: np.ndarray            # (k, n_E) loadings
    scores: np.ndarray                # (n_samples, k)
    mean: np.ndarray

    @property
    def cumulative(self) -> np.ndarray:
        return np.cumsum(self.explained)


def pca(D: np.ndarray, n_components: int | None = None) -> PcaResult:
    """SVD of the mean-centred sample x energy matrix."""
    D = np.asarray(D, float)
    mean = D.mean(axis=0)
    U, s, Vt = np.linalg.svd(D - mean, full_matrices=False)
    var = s ** 2
    k = n_components or len(s)
    return PcaResult(explained=var / var.sum() if var.sum() else var, components=Vt[:k], scores=(U * s)[:, :k], mean=mean)


# ----------------------------------------------------------------------------- MCR-ALS
@dataclass
class McrResult:
    C: np.ndarray                     # (n_samples, k) concentrations
    ST: np.ndarray                    # (k, n_E) component spectra
    err: list[float]
    n_iter: int
    converged: bool
    rmse: float

    @property
    def proportions(self) -> np.ndarray:
        """Per sample, each component's share of the sum of concentrations (percent)."""
        s = self.C.sum(axis=1, keepdims=True)
        with np.errstate(all="ignore"):
            return np.where(s > 0, 100 * self.C / s, 0.0)


def _init_st(D: np.ndarray, k: int) -> np.ndarray:
    """Initial spectra: the k samples furthest apart (a SIMPLISMA-like pick), made non-negative."""
    n = D.shape[0]
    picks = [int(np.argmax(np.linalg.norm(D, axis=1)))]
    while len(picks) < min(k, n):
        d = np.min([np.linalg.norm(D - D[p], axis=1) for p in picks], axis=0)
        picks.append(int(np.argmax(d)))
    ST = np.clip(D[picks], 0, None)
    while ST.shape[0] < k:
        ST = np.vstack([ST, np.abs(np.random.default_rng(ST.shape[0]).normal(size=D.shape[1]))])
    return ST


def mcr_als(D: np.ndarray, n_components: int, max_iter: int = 100, tol_err_change: float = 1e-6,
            nonneg: bool = True, clip_negative: bool = False, ST0: np.ndarray | None = None,
            c_fixed: dict[int, np.ndarray] | None = None) -> McrResult:
    """Alternating least squares D = C ST with non-negativity on both (NNLS), John's defaults.
    `c_fixed` = {sample index: concentration row} rows held fixed (ITFA)."""
    D = np.asarray(D, float)
    if clip_negative:
        D = np.clip(D, 0, None)
    n, m = D.shape
    k = n_components
    ST = np.asarray(ST0, float) if ST0 is not None else _init_st(D, k)
    C = np.zeros((n, k))
    err: list[float] = []
    converged = False
    c_fixed = c_fixed or {}
    for it in range(max_iter):
        for i in range(n):
            if i in c_fixed:
                C[i] = c_fixed[i]
            elif nonneg:
                C[i], _ = nnls(ST.T, D[i])
            else:
                C[i] = np.linalg.lstsq(ST.T, D[i], rcond=None)[0]
        for j in range(m):
            if nonneg:
                ST[:, j], _ = nnls(C, D[:, j])
            else:
                ST[:, j] = np.linalg.lstsq(C, D[:, j], rcond=None)[0]
        e = float(np.sqrt(np.mean((D - C @ ST) ** 2)))
        err.append(e)
        if len(err) > 1 and abs(err[-2] - e) < tol_err_change:
            converged = True
            break
    return McrResult(C=C, ST=ST, err=err, n_iter=len(err), converged=converged, rmse=err[-1] if err else float("nan"))


def itfa(D: np.ndarray, mcr: McrResult, targets: dict[int, np.ndarray], max_iter: int = 100,
         tol_err_change: float = 1e-6) -> McrResult:
    """Iterative target factor analysis as John's tab 6 does it: refine the MCR-ALS solution with some samples'
    concentration rows anchored (targets = {sample index: concentrations summing like the others})."""
    return mcr_als(D, mcr.ST.shape[0], max_iter=max_iter, tol_err_change=tol_err_change, ST0=mcr.ST.copy(), c_fixed=targets)


def prepare_matrix(curves: list[tuple[np.ndarray, np.ndarray]], fit_range: tuple[float, float] | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(E grid, D) with every curve interpolated onto the common overlap."""
    grid = common_grid(curves, fit_range)
    D = np.array([on_grid(E, y, grid) for E, y in curves])
    ok = np.all(np.isfinite(D), axis=0)
    return grid[ok], D[:, ok]
