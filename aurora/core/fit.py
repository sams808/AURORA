"""Fits of John Bussey's tabs 4-6: linear combination fitting, PCA, MCR-ALS, ITFA, plus a peak decomposition.
numpy / scipy only.

All spectra are interpolated onto the common energy grid of the overlap before fitting; the caller passes
(E, y) pairs already normalised and shifted by the Compare perspective (a target may carry a third array, its
uncertainty).

LCF follows the Larch / Athena conventions: weighted least squares (weights 1/sigma^2), chi-square, reduced chi-square
= chi^2 / (N - p), the R-factor sum(resid^2) / sum(target^2), and uncertainties from the covariance scaled by
sqrt(reduced chi-square). A bootstrap can replace them: moving blocks of residuals as long as the residual
autocorrelation (the default when bootstrapping, so correlated residuals are not mistaken for independent points) or
single points (iid). Nested fits are compared with an F-test (Hamilton). Long loops take a progress(i, n, message)
callback, which raises aurora.core.cancel.Cancelled when the user cancels.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize, nnls

ENUMERATE_MAX = 8                  # up to this many components every active set is checked (exact); beyond: SLSQP / NNLS
BOUND_TOL = 1e-9                   # a coefficient below this (x the largest one) sits at the 0 bound


def common_grid(curves: list[tuple], fit_range: tuple[float, float] | None = None) -> np.ndarray:
    """Energies of the first curve inside the overlap of all curves (and the fit range)."""
    lo = max(float(np.nanmin(c[0])) for c in curves)
    hi = min(float(np.nanmax(c[0])) for c in curves)
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


def _tick(progress, i: int, n: int, msg: str) -> None:
    if progress is not None:
        progress(i, n, msg)


# ----------------------------------------------------------------------------- noise and correlation
def estimate_noise(y: np.ndarray) -> float:
    """Point-to-point scatter of a spectrum from its second differences (robust, MAD): for white noise of standard
    deviation s, y[i-1] - 2 y[i] + y[i+1] has standard deviation s * sqrt(6). A smooth signal contributes little."""
    y = np.asarray(y, float)
    y = y[np.isfinite(y)]
    if y.size < 5:
        return float("nan")
    d2 = y[:-2] - 2 * y[1:-1] + y[2:]
    mad = float(np.median(np.abs(d2 - np.median(d2))))
    return 1.4826 * mad / math.sqrt(6.0)


def autocorrelation_length(r: np.ndarray) -> float:
    """Integrated autocorrelation time of a residual (in points): 1 + 2 * sum of the autocorrelations over the initial
    positive sequence (lags until the first non-positive one, at most N/4). 1 for white noise."""
    r = np.asarray(r, float)
    r = r[np.isfinite(r)]
    n = r.size
    if n < 8:
        return 1.0
    r = r - r.mean()
    c0 = float(np.dot(r, r) / n)
    if c0 <= 0:
        return 1.0
    tau = 1.0
    for k in range(1, max(2, n // 4)):
        rho = float(np.dot(r[:-k], r[k:]) / n) / c0
        if rho <= 0:
            break
        tau += 2 * rho
    return max(1.0, tau)


# ----------------------------------------------------------------------------- LCF solver
class _Solver:
    """min |Aw c - tw|^2 with optional c >= 0 and sum(c) = 1, for one target or many (bootstrap draws).

    Up to ENUMERATE_MAX components every support (set of non-zero coefficients) is solved from the Gram matrix and
    the best feasible one is kept: the exact optimum of the convex problem. Larger sets use SLSQP / NNLS."""

    def __init__(self, Aw: np.ndarray, sum_to_one: bool, allow_negative: bool):
        self.Aw = Aw
        self.n = Aw.shape[1]
        self.sum_to_one = sum_to_one
        self.allow_negative = allow_negative
        self.G = Aw.T @ Aw
        self.exact = allow_negative or self.n <= ENUMERATE_MAX
        self.message = ""
        self._sets = []
        if not allow_negative and self.exact:
            for k in range(1, self.n + 1):
                for S in itertools.combinations(range(self.n), k):
                    self._sets.append(self._prepare(list(S)))

    @staticmethod
    def _inv(K: np.ndarray) -> np.ndarray:
        try:
            return np.linalg.inv(K)
        except np.linalg.LinAlgError:                      # collinear components: the least-norm solution
            return np.linalg.pinv(K)

    def _prepare(self, S):
        G = self.G[np.ix_(S, S)]
        if self.sum_to_one:
            m = len(S)
            lam = float(np.sqrt(max(np.trace(G) / m, 1e-300)))     # the constraint rows on the scale of G
            K = np.zeros((m + 1, m + 1))
            K[:m, :m] = G
            K[:m, m] = lam
            K[m, :m] = lam
            Ki = self._inv(K)
            M = np.hstack([Ki[:m, :m], Ki[:m, m:] * lam])  # c_S = M @ [b_S; 1]
        else:
            M = self._inv(G)                               # c_S = M @ b_S
        return S, G, M

    def refine(self, tw: np.ndarray, c: np.ndarray) -> np.ndarray:
        """The coefficients on the support of c recomputed by least squares on the design itself (better conditioned
        than the Gram matrix); with sum to 1 the largest one is the complement of the others."""
        S = np.flatnonzero(c > 0) if not self.allow_negative else np.arange(self.n)
        if S.size == 0:
            return c
        out = np.zeros(self.n)
        if self.sum_to_one:
            j = S[int(np.argmax(c[S]))]
            others = [i for i in S if i != j]
            if others:
                Bm = self.Aw[:, others] - self.Aw[:, [j]]
                x = np.linalg.lstsq(Bm, tw - self.Aw[:, j], rcond=None)[0]
                out[others] = x
                out[j] = 1.0 - x.sum()
            else:
                out[j] = 1.0
        else:
            out[S] = np.linalg.lstsq(self.Aw[:, S], tw, rcond=None)[0]
        if not self.allow_negative and np.any(out < -1e-12):
            return c                                       # the refinement left the feasible set: keep the exact one
        return np.clip(out, 0.0, None) if not self.allow_negative else out

    def solve_many(self, T: np.ndarray) -> np.ndarray:
        """Coefficients (n, D) for the weighted targets T (N, D)."""
        B = self.Aw.T @ T                                  # (n, D)
        D = T.shape[1]
        if self.allow_negative:
            if self.sum_to_one:
                K = np.zeros((self.n + 1, self.n + 1))
                K[:self.n, :self.n] = self.G
                K[:self.n, self.n] = 1.0
                K[self.n, :self.n] = 1.0
                rhs = np.vstack([B, np.ones((1, D))])
                return np.linalg.lstsq(K, rhs, rcond=None)[0][:self.n]
            return np.linalg.lstsq(self.Aw, T, rcond=None)[0]
        if not self.exact:
            return np.column_stack([self._iterative(T[:, j]) for j in range(D)])
        best = np.full(D, np.inf)
        out = np.zeros((self.n, D))
        if not self.sum_to_one:
            best = np.zeros(D)                             # the empty support: c = 0, objective 0
        for S, G, M in self._sets:
            bS = B[S]
            C = M[:, :len(S)] @ bS + M[:, len(S)][:, None] if self.sum_to_one else M @ bS
            ok = np.all(C >= -1e-12, axis=0)
            if not ok.any():
                continue
            obj = np.einsum("id,ij,jd->d", C, G, C) - 2 * np.einsum("id,id->d", C, bS)
            better = ok & (obj < best)
            if better.any():
                best = np.where(better, obj, best)
                cols = np.flatnonzero(better)
                out[:, cols] = 0.0
                out[np.ix_(S, cols)] = np.clip(C[:, cols], 0.0, None)
        return out

    def _iterative(self, tw: np.ndarray) -> np.ndarray:
        Aw, n = self.Aw, self.n
        if not self.sum_to_one:
            c, _ = nnls(Aw, tw)
            return c
        r = minimize(lambda c: float(((Aw @ c - tw) ** 2).sum()), np.full(n, 1.0 / n), bounds=[(0.0, None)] * n,
                     constraints=[{"type": "eq", "fun": lambda c: c.sum() - 1.0}], method="SLSQP",
                     options={"maxiter": 500, "ftol": 1e-12})
        if not r.success:
            self.message = f"SLSQP: {r.message}"
        return r.x

    def solve(self, tw: np.ndarray) -> np.ndarray:
        c = self.solve_many(tw[:, None])[:, 0]
        return self.refine(tw, c) if self.exact else c


def _covariance(Aw: np.ndarray, c: np.ndarray, free: np.ndarray, sum_to_one: bool) -> np.ndarray:
    """Covariance (n, n) of the coefficients from the weighted design, over the free (not bound) ones; bound
    coefficients are held fixed (zero rows). With sum to 1 one free coefficient is the complement of the others."""
    n = Aw.shape[1]
    cov = np.zeros((n, n))
    F = np.flatnonzero(free)
    if F.size == 0:
        return cov
    if sum_to_one:
        if F.size == 1:
            return cov                                     # the only free coefficient is 1 exactly
        j = F[int(np.argmax(c[F]))]
        others = [i for i in F if i != j]
        Bm = Aw[:, others] - Aw[:, [j]]
        cp = np.linalg.pinv(Bm.T @ Bm)
        J = np.zeros((n, len(others)))
        for k, i in enumerate(others):
            J[i, k] = 1.0
            J[j, k] = -1.0
        return J @ cp @ J.T
    cf = np.linalg.pinv(Aw[:, F].T @ Aw[:, F])
    cov[np.ix_(F, F)] = cf
    return cov


@dataclass
class FTest:
    """Hamilton's F-test of the full fit against the fit without one component."""
    component: int
    F: float
    p_value: float
    dof1: int
    dof2: float
    chi2_without: float

    def improves(self, alpha: float = 0.05) -> bool:
        return bool(np.isfinite(self.p_value) and self.p_value < alpha)


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
    chi2: float = float("nan")
    redchi: float = float("nan")
    n_params: int = 0
    sigma: np.ndarray | None = None
    sigma_source: str = ""
    method: str = "covariance"        # 'covariance' | 'block bootstrap' | 'iid bootstrap'
    n_draws: int = 0
    block_length: int = 1
    autocorr_length: float = 1.0
    covariance: np.ndarray | None = None
    cov_uncertainties: np.ndarray | None = None
    converged: bool = True
    message: str = ""
    at_bound: np.ndarray | None = None
    sum_sigma: float = float("nan")
    flags: list[str] = field(default_factory=list)
    f_tests: list[FTest] = field(default_factory=list)
    sum_to_one: bool = True
    allow_negative: bool = False

    @property
    def total(self) -> float:
        return float(self.coefficients.sum())

    @property
    def undetermined(self) -> np.ndarray:
        """Coefficients whose uncertainty exceeds their value (those at a bound are flagged separately)."""
        u = np.asarray(self.uncertainties, float)
        bound = self.at_bound if self.at_bound is not None else np.zeros(len(u), bool)
        return (~bound) & np.isfinite(u) & (u > np.abs(self.coefficients))

    def uncertainty_text(self) -> str:
        if self.method == "block bootstrap":
            return (f"± from {self.n_draws} bootstrap draws (moving blocks of {self.block_length} points, the "
                    f"residual autocorrelation length)")
        if self.method == "iid bootstrap":
            return f"± from {self.n_draws} bootstrap draws (single points resampled)"
        return "± from the covariance, scaled by √(reduced χ²)"


def _fit_once(A, t, sigma, sum_to_one, allow_negative):
    Aw, tw = A / sigma[:, None], t / sigma
    s = _Solver(Aw, sum_to_one, allow_negative)
    c = s.solve(tw)
    chi2 = float(((((A @ c) - t) / sigma) ** 2).sum())
    return c, chi2, s


def f_test(chi2_small: float, p_small: int, chi2_big: float, p_big: int, n: int, tau: float = 1.0) -> FTest:
    """Hamilton's F-test for nested fits on the same N points: F = ((chi2_small - chi2_big) / (p_big - p_small)) /
    (chi2_big / (N - p_big)). With correlated residuals (integrated autocorrelation tau > 1) N is replaced by N / tau
    and F by F / tau, so neighbouring points are not counted as independent evidence."""
    from scipy.stats import f as fdist
    d1 = max(1, int(p_big - p_small))
    tau = max(1.0, float(tau))
    d2 = max(1.0, n / tau - p_big)
    if chi2_big <= 0 or not np.isfinite(chi2_big):
        return FTest(-1, float("inf"), 0.0, d1, d2, chi2_small)
    F = max(0.0, (chi2_small - chi2_big) / d1) / (chi2_big / max(1.0, n - p_big)) / tau
    return FTest(-1, float(F), float(fdist.sf(F, d1, d2)), d1, d2, chi2_small)


def lcf(target: tuple, components: list[tuple[np.ndarray, np.ndarray]],
        fit_range: tuple[float, float] | None = None, sum_to_one: bool = True, allow_negative: bool = False,
        bootstrap: int = 200, seed: int = 0, sigma=None, resample: str = "block", f_tests: bool = True,
        progress=None) -> LcfResult:
    """Weighted least squares of the target on the components (weights 1/sigma^2).

    sigma: the target's uncertainty, from `sigma` (scalar or array on the target's energies), else a third array of
    `target` (Spectrum.meta 'err'), else estimated from the target's point-to-point scatter (the result says which).
    Coefficients >= 0 unless allow_negative, summing to 1 when sum_to_one. Uncertainty: the covariance scaled by
    sqrt(reduced chi-square) (bootstrap = 0), or the standard deviation over `bootstrap` refits of residuals resampled
    in moving blocks as long as their autocorrelation (resample='block', the default) or point by point ('iid')."""
    Et, yt = target[0], target[1]
    et = target[2] if len(target) > 2 else None
    grid = common_grid([(Et, yt)] + list(components), fit_range)
    t = on_grid(Et, yt, grid)
    A = np.column_stack([on_grid(E, y, grid) for E, y in components])
    if sigma is not None:
        s_arr = np.broadcast_to(np.asarray(sigma, float), np.shape(Et)) if np.ndim(sigma) else np.full(len(Et), float(sigma))
        sig = on_grid(Et, np.asarray(s_arr, float), grid)
        source = "given"
    elif et is not None and np.isfinite(et).any() and np.nanmax(et) > 0:
        sig = on_grid(Et, np.asarray(et, float), grid)
        source = "the target's own uncertainties"
    else:
        sig = None
        source = ""
    ok = np.isfinite(t) & np.all(np.isfinite(A), axis=1)
    if sig is not None:
        ok &= np.isfinite(sig) & (sig > 0)
    t, A, grid = t[ok], A[ok], grid[ok]
    if t.size < 3:
        raise ValueError("fewer than 3 points where every spectrum is defined")
    n = A.shape[1]
    N = int(t.size)
    if sig is None:
        s0 = estimate_noise(t)
        if np.isfinite(s0) and s0 > 0:
            sig = np.full(N, s0)
            source = "estimated from the target's point-to-point scatter (second differences)"
        else:
            sig = np.ones(N)
            source = "unweighted (no scatter measurable on the target)"
    else:
        sig = sig[ok]
    p = n - (1 if sum_to_one else 0)
    _tick(progress, 0, max(1, bootstrap), "fitting")
    c, chi2, solver = _fit_once(A, t, sig, sum_to_one, allow_negative)
    fit = A @ c
    resid = t - fit
    rmse = float(np.sqrt(np.mean(resid ** 2)))
    r_factor = float((resid ** 2).sum() / max((t ** 2).sum(), 1e-300))
    dof = max(1, N - p)
    redchi = chi2 / dof
    scale_c = max(abs(float(np.max(np.abs(c)))), 1.0) if c.size else 1.0
    at_bound = (c <= BOUND_TOL * scale_c) if not allow_negative else np.zeros(n, bool)
    Aw = A / sig[:, None]
    cov = _covariance(Aw, c, ~at_bound, sum_to_one) * redchi
    cov_unc = np.sqrt(np.clip(np.diag(cov), 0, None))
    cov_unc = np.where(at_bound, np.nan, cov_unc)
    z = resid / sig
    tau = autocorrelation_length(z)
    block = max(1, int(round(tau)))
    unc = cov_unc.copy()
    method, draws_n = "covariance", 0
    sums = None
    if bootstrap and N > p + 2:
        rng = np.random.default_rng(seed)
        draws = []
        chunk = 25
        done = 0
        while done < bootstrap:
            m = min(chunk, bootstrap - done)
            Z = np.empty((N, m))
            for j in range(m):
                if resample == "iid":
                    Z[:, j] = z[rng.integers(0, N, N)]
                else:
                    L = min(block, N)
                    starts = rng.integers(0, N - L + 1, size=int(math.ceil(N / L)))
                    idx = (starts[:, None] + np.arange(L)).ravel()[:N]
                    Z[:, j] = z[idx]
            T = (fit[:, None] + sig[:, None] * Z) / sig[:, None]
            draws.append(solver.solve_many(T).T)
            done += m
            _tick(progress, done, bootstrap, f"bootstrap draw {done} of {bootstrap}")
        D = np.vstack(draws)
        unc = D.std(axis=0)
        sums = D.sum(axis=1)
        method, draws_n = ("iid bootstrap" if resample == "iid" else "block bootstrap"), int(bootstrap)
    flags = []
    names = [f"component {i + 1}" for i in range(n)]
    for i in np.flatnonzero(at_bound):
        flags.append(f"{names[i]} at the 0 bound")
    sum_sigma = float("nan")
    if not sum_to_one:
        sum_sigma = float(np.std(sums)) if sums is not None else float(np.sqrt(max(0.0, np.ones(n) @ cov @ np.ones(n))))
        if np.isfinite(sum_sigma) and sum_sigma > 0 and abs(c.sum() - 1.0) > 3 * sum_sigma:
            flags.append(f"the coefficients sum to {c.sum():.3f} ± {sum_sigma:.3f}, outside 1 ± 3σ")
    tests: list[FTest] = []
    if f_tests and n >= 2:
        for k in range(n):
            keep = [i for i in range(n) if i != k]
            ck, chi2_k, _s = _fit_once(A[:, keep], t, sig, sum_to_one, allow_negative)
            ft = f_test(chi2_k, p - 1, chi2, p, N, tau)
            ft.component = k
            tests.append(ft)
    converged = not solver.message
    return LcfResult(c, unc, grid, t, fit, A.T, rmse, r_factor, N, chi2=chi2, redchi=redchi, n_params=p, sigma=sig,
                     sigma_source=source, method=method, n_draws=draws_n, block_length=block, autocorr_length=tau,
                     covariance=cov, cov_uncertainties=cov_unc, converged=converged,
                     message=solver.message or ("exact: every active set checked" if solver.exact else ""),
                     at_bound=at_bound, sum_sigma=sum_sigma, flags=flags, f_tests=tests, sum_to_one=sum_to_one,
                     allow_negative=allow_negative)


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
    E: np.ndarray | None = None       # the energies, matrix and row labels of the run (ITFA works on the same ones)
    D: np.ndarray | None = None
    labels: list[str] = field(default_factory=list)
    message: str = ""
    clip_negative: bool = False

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
            c_fixed: dict[int, np.ndarray] | None = None, progress=None, E: np.ndarray | None = None,
            labels: list[str] | None = None) -> McrResult:
    """Alternating least squares D = C ST with non-negativity on both (NNLS), John's defaults.
    `c_fixed` = {sample index: concentration row} rows held fixed (ITFA). The result keeps E, D and the labels."""
    D_in = np.asarray(D, float)
    D = np.clip(D_in, 0, None) if clip_negative else D_in
    n, m = D.shape
    k = n_components
    ST = np.asarray(ST0, float).copy() if ST0 is not None else _init_st(D, k)
    C = np.zeros((n, k))
    err: list[float] = []
    converged = False
    c_fixed = c_fixed or {}
    for it in range(max_iter):
        _tick(progress, it, max_iter, f"MCR-ALS iteration {it + 1} of {max_iter}")
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
    msg = "" if converged else (f"max iterations reached ({max_iter}); the error still changed by "
                                f"{abs(err[-2] - err[-1]) if len(err) > 1 else float('nan'):.2g} per iteration")
    return McrResult(C=C, ST=ST, err=err, n_iter=len(err), converged=converged, rmse=err[-1] if err else float("nan"),
                     E=None if E is None else np.asarray(E, float), D=D_in, labels=list(labels or []), message=msg,
                     clip_negative=clip_negative)


def itfa(D: np.ndarray | None, mcr: McrResult, targets: dict[int, np.ndarray], max_iter: int = 100,
         tol_err_change: float = 1e-6, progress=None) -> McrResult:
    """Iterative target factor analysis as John's tab 6 does it: refine the MCR-ALS solution with some samples'
    concentration rows anchored (targets = {sample index: concentrations summing like the others}). D = None uses the
    matrix of the MCR run."""
    if D is None:
        if mcr.D is None:
            raise ValueError("the MCR result has no matrix: rerun MCR first")
        D = mcr.D
    return mcr_als(D, mcr.ST.shape[0], max_iter=max_iter, tol_err_change=tol_err_change, ST0=mcr.ST.copy(),
                   c_fixed=targets, progress=progress, E=mcr.E, labels=mcr.labels, clip_negative=mcr.clip_negative)


def same_matrix(mcr: McrResult, labels: list[str], E: np.ndarray, D: np.ndarray) -> bool:
    """True when (labels, E, D) are the ones of the MCR run (ITFA refuses otherwise)."""
    if mcr.D is None or list(labels) != list(mcr.labels) or mcr.E is None:
        return False
    if np.shape(E) != np.shape(mcr.E) or np.shape(D) != np.shape(mcr.D):
        return False
    return bool(np.allclose(E, mcr.E) and np.allclose(D, mcr.D, equal_nan=True))


def prepare_matrix(curves: list[tuple[np.ndarray, np.ndarray]], fit_range: tuple[float, float] | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(E grid, D) with every curve interpolated onto the common overlap."""
    grid = common_grid(curves, fit_range)
    D = np.array([on_grid(E, y, grid) for E, y in curves])
    ok = np.all(np.isfinite(D), axis=0)
    return grid[ok], D[:, ok]


# ----------------------------------------------------------------------------- peak decomposition
# Every shape is parameterised by its full width at half maximum (FWHM, eV) and its height a.
_G = 4.0 * math.log(2.0)
_GAUSS_AREA = math.sqrt(math.pi / _G)          # area of a unit-height Gaussian of FWHM 1
_LORENTZ_AREA = math.pi / 2.0                  # area of a unit-height Lorentzian of FWHM 1


def _gauss(E, a, c, fwhm):
    return a * np.exp(-_G * ((E - c) / max(fwhm, 1e-6)) ** 2)


def _lorentz(E, a, c, fwhm):
    return a / (1.0 + 4.0 * ((E - c) / max(fwhm, 1e-6)) ** 2)


def _pvoigt(E, a, c, fwhm, eta):
    return eta * _lorentz(E, a, c, fwhm) + (1.0 - eta) * _gauss(E, a, c, fwhm)


SHAPES = {"gaussian": _gauss, "lorentzian": _lorentz, "pseudo-voigt": _pvoigt}
SHAPE_PARAMS = {"gaussian": ("amp", "centre", "fwhm"), "lorentzian": ("amp", "centre", "fwhm"),
                "pseudo-voigt": ("amp", "centre", "fwhm", "eta")}
UNITS = {"amp": "a.u.", "centre": "eV", "fwhm": "eV", "eta": "fraction", "area": "a.u.·eV", "bg0": "a.u.",
         "bg_slope": "a.u./eV", "step_height": "a.u.", "step_centre": "eV", "step_width": "eV"}


def unit_of(name: str) -> str:
    """The unit of a peak parameter: 'eV' for centres, FWHM and the step centre and width, 'a.u.' for amplitudes."""
    if name in UNITS:
        return UNITS[name]
    return UNITS.get(name.split("_", 1)[1] if "_" in name else name, "")


def peak_area(shape: str, a: float, fwhm: float, eta: float = 0.5) -> float:
    if shape == "gaussian":
        return a * fwhm * _GAUSS_AREA
    if shape == "lorentzian":
        return a * fwhm * _LORENTZ_AREA
    return a * fwhm * (eta * _LORENTZ_AREA + (1.0 - eta) * _GAUSS_AREA)


@dataclass
class PeakFit:
    E: np.ndarray
    y: np.ndarray
    fit: np.ndarray
    components: dict[str, np.ndarray]
    params: dict[str, float]
    errors: dict[str, float]
    rmse: float
    converged: bool
    message: str = ""
    initial: dict[str, float] = field(default_factory=dict)
    areas: dict[str, tuple[float, float]] = field(default_factory=dict)
    lower: dict[str, float] = field(default_factory=dict)
    upper: dict[str, float] = field(default_factory=dict)
    shape: str = "gaussian"

    @property
    def undetermined(self) -> list[str]:
        """Parameters whose standard error exceeds their value (or has none)."""
        return [k for k, v in self.params.items()
                if not np.isfinite(self.errors.get(k, np.nan)) or self.errors.get(k, np.nan) > abs(v)]

    @property
    def centres_at_bound(self) -> list[str]:
        out = []
        for k, v in self.params.items():
            if k.endswith("_centre") and k in self.lower and k in self.upper:
                span = max(self.upper[k] - self.lower[k], 1e-12)
                if v <= self.lower[k] + 1e-6 * span or v >= self.upper[k] - 1e-6 * span:
                    out.append(k)
        return out

    def initial_centres(self) -> list[float]:
        return [v for k, v in self.initial.items() if k.startswith("p") and k.endswith("_centre")]


def _covariance_of(model, E: np.ndarray, y: np.ndarray, popt: np.ndarray, rel_tol: float = 1e-8) -> np.ndarray:
    """Covariance of least-squares parameters from the Jacobian at the optimum, scaled by the residual variance (as
    curve_fit does), except that a parameter the data cannot see (a direction of the Jacobian's null space: a peak
    of zero height, one narrower than the energy step) gets an infinite variance instead of the zero that a truncated
    pseudo-inverse would report."""
    p = np.asarray(popt, float)
    n, m = E.size, p.size
    J = np.empty((n, m))
    for j in range(m):
        h = 1e-6 * max(1.0, abs(p[j]))
        a, b = p.copy(), p.copy()
        a[j] += h; b[j] -= h
        J[:, j] = (np.asarray(model(E, *a), float) - np.asarray(model(E, *b), float)) / (2 * h)
    resid = y - np.asarray(model(E, *p), float)
    s_sq = float(resid @ resid) / max(1, n - m)
    if not np.all(np.isfinite(J)):
        return np.full((m, m), np.inf)
    U, S, Vt = np.linalg.svd(J, full_matrices=False)
    if S.size == 0 or S[0] <= 0:
        return np.full((m, m), np.inf)
    keep = S > rel_tol * S[0]
    cov = (Vt[keep].T / S[keep] ** 2) @ Vt[keep] * s_sq
    if (~keep).any():
        weight = (Vt[~keep] ** 2).sum(axis=0)          # how much of each parameter lies in the null space
        blind = weight > 1e-6
        cov[blind, :] = np.inf
        cov[:, blind] = np.inf
    return cov


def peak_decomposition(E: np.ndarray, y: np.ndarray, n_peaks: int = 2, shape: str = "gaussian", step: bool = True,
                       background: str = "linear", init_centres: list[float] | None = None, init_width: float = 3.5,
                       fit_range: tuple[float, float] | None = None, max_nfev: int = 20000) -> PeakFit:
    """Peaks (gaussian / lorentzian / pseudo-voigt, each by height, centre and FWHM in eV; the pseudo-voigt also fits
    its Lorentzian fraction eta in [0, 1]) + an arctan edge step (height, centre, width = its 25-75 % rise, which is
    the FWHM of its derivative) + a linear background, least squares with bounds (scipy curve_fit); initial centres
    from the most prominent peaks when not given. Errors and peak areas (with errors) from the covariance."""
    from scipy.optimize import curve_fit
    from scipy.signal import find_peaks
    E = np.asarray(E, float); y = np.asarray(y, float)
    sel = np.isfinite(E) & np.isfinite(y)
    if fit_range:
        sel &= (E >= fit_range[0]) & (E <= fit_range[1])
    E, y = E[sel], y[sel]
    if E.size < 5:
        raise ValueError("not enough points to fit")
    f = SHAPES[shape]
    pnames = SHAPE_PARAMS[shape]
    npp = len(pnames)
    typed = [float(c) for c in (init_centres or [])]
    init_centres = list(typed)
    if len(init_centres) < n_peaks:                    # the typed centres first, then the most prominent maxima
        idx, props = find_peaks(y, prominence=0.02 * (np.nanmax(y) - np.nanmin(y) + 1e-12))
        order = np.argsort(props["prominences"])[::-1] if idx.size else np.array([], int)
        step_e = float(np.median(np.diff(E))) if E.size > 1 else 0.0
        for i in order:
            if len(init_centres) >= n_peaks:
                break
            c = float(E[idx[i]])
            if all(abs(c - t) > max(1.0, 2 * step_e) for t in init_centres):
                init_centres.append(c)
        while len(init_centres) < n_peaks:
            init_centres.append(float(E[0] + (len(init_centres) + 1) * (E[-1] - E[0]) / (n_peaks + 1)))
    n_peaks = len(init_centres)
    y0, y1 = float(np.nanmean(y[: max(3, E.size // 10)])), float(np.nanmean(y[-max(3, E.size // 10):]))
    amp0 = float(np.nanmax(y) - min(y0, y1))
    linear = background == "linear"
    span = float(E[-1] - E[0])

    def model(E, *p):
        out = p[0] + (p[1] * (E - E[0]) if linear else 0.0)
        k = 2 if linear else 1
        if step:
            out = out + p[k] * (0.5 + np.arctan(2.0 * (E - p[k + 1]) / max(p[k + 2], 1e-6)) / np.pi); k += 3
        for i in range(n_peaks):
            out = out + f(E, *p[k + npp * i: k + npp * (i + 1)])
        return out

    names, p0, lo, hi = ["bg0"], [min(y0, y1)], [-np.inf], [np.inf]
    if linear:
        names += ["bg_slope"]; p0 += [0.0]; lo += [-np.inf]; hi += [np.inf]
    if step:
        c0 = float(E[int(np.argmax(np.gradient(y, E)))])
        names += ["step_height", "step_centre", "step_width"]; p0 += [max(y1 - y0, 1e-3), c0, 2.0]
        lo += [0.0, E[0], 0.1]; hi += [np.inf, E[-1], span]
    for i, c in enumerate(init_centres):
        names += [f"p{i + 1}_amp", f"p{i + 1}_centre", f"p{i + 1}_fwhm"]
        p0 += [max(amp0, 1e-3), min(max(c, E[0]), E[-1]), min(max(init_width, 0.1), span)]
        lo += [0.0, E[0], 0.05]; hi += [np.inf, E[-1], span]
        if shape == "pseudo-voigt":
            names += [f"p{i + 1}_eta"]; p0 += [0.5]; lo += [0.0]; hi += [1.0]
    initial = dict(zip(names, map(float, p0)))
    pcov = None
    try:
        popt, _pcov, info, mesg, ier = curve_fit(model, E, y, p0=p0, bounds=(lo, hi), max_nfev=max_nfev, full_output=True)
        pcov = _covariance_of(model, E, y, popt)
        err = np.sqrt(np.abs(np.diag(pcov)))
        converged = ier in (1, 2, 3, 4)
        msg = "" if converged else str(mesg)
    except (RuntimeError, ValueError) as e:
        popt = np.array(p0, float); err = np.full(len(p0), np.nan); converged = False; msg = str(e)
    fit = model(E, *popt)
    comps = {}
    k = 2 if linear else 1
    comps["background"] = popt[0] + (popt[1] * (E - E[0]) if linear else np.zeros_like(E))
    if step:
        comps["step"] = popt[k] * (0.5 + np.arctan(2.0 * (E - popt[k + 1]) / max(popt[k + 2], 1e-6)) / np.pi); k += 3
    areas = {}
    for i in range(n_peaks):
        sl = slice(k + npp * i, k + npp * (i + 1))
        pp = popt[sl]
        comps[f"peak {i + 1}"] = f(E, *pp)
        eta = float(pp[3]) if npp == 4 else 0.5
        area = peak_area(shape, float(pp[0]), float(pp[2]), eta)
        a_err = float("nan")
        idx = [k + npp * i, k + npp * i + 2] + ([k + npp * i + 3] if npp == 4 else [])
        if pcov is not None and not np.all(np.isfinite(pcov[np.ix_(idx, idx)])):
            a_err = float("inf")
        elif pcov is not None:
            if shape == "gaussian":
                g = [pp[2] * _GAUSS_AREA, pp[0] * _GAUSS_AREA]
            elif shape == "lorentzian":
                g = [pp[2] * _LORENTZ_AREA, pp[0] * _LORENTZ_AREA]
            else:
                w = eta * _LORENTZ_AREA + (1 - eta) * _GAUSS_AREA
                g = [pp[2] * w, pp[0] * w, pp[0] * pp[2] * (_LORENTZ_AREA - _GAUSS_AREA)]
            g = np.asarray(g, float)
            sub = pcov[np.ix_(idx, idx)]
            a_err = float(np.sqrt(max(0.0, g @ sub @ g)))
        areas[f"p{i + 1}_area"] = (float(area), a_err)
    return PeakFit(E=E, y=y, fit=fit, components=comps, params=dict(zip(names, map(float, popt))),
                   errors=dict(zip(names, map(float, err))), rmse=float(np.sqrt(np.mean((y - fit) ** 2))),
                   converged=converged, message=msg, initial=initial, areas=areas,
                   lower=dict(zip(names, map(float, lo))), upper=dict(zip(names, map(float, hi))), shape=shape)


# ----------------------------------------------------------------------------- what a fit says about itself
def fit_status(converged: bool, message: str = "", n_undetermined: int = 0) -> tuple[str, str]:
    """(chip text, tone) of a fit: 'converged' (ok), 'not converged: <message>' (bad), or 'converged · N parameters
    undetermined (σ > |value|)' (warn)."""
    if not converged:
        return f"not converged: {message or 'the optimiser stopped'}", "bad"
    if n_undetermined:
        word = "parameter" if n_undetermined == 1 else "parameters"
        return f"converged · {n_undetermined} {word} undetermined (σ > |value|)", "warn"
    return "converged", "ok"
