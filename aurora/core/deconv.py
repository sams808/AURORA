"""Map deconvolution (CLS dashboard feature): Richardson-Lucy and Wiener with a Gaussian point spread function,
pure numpy / scipy. Works on a gridded image (NaN outside the measured points)."""
from __future__ import annotations

import numpy as np
from scipy import ndimage


def gaussian_psf(fwhm_px: float, size: int | None = None) -> np.ndarray:
    sigma = max(float(fwhm_px), 1e-3) / 2.3548
    size = size or int(2 * np.ceil(3 * sigma) + 1)
    r = np.arange(size) - size // 2
    g = np.exp(-0.5 * (r[:, None] ** 2 + r[None, :] ** 2) / sigma ** 2)
    return g / g.sum()


def richardson_lucy(img: np.ndarray, psf: np.ndarray, iterations: int = 20, eps: float = 1e-12,
                    progress=None) -> np.ndarray:
    """Classic RL with reflected boundaries; NaN pixels are filled with the finite mean before and restored after.
    `progress(i, n, msg)` is called once per iteration (a job's callback, which may raise Cancelled)."""
    img = np.asarray(img, float)
    fin = np.isfinite(img)
    fill = float(np.nanmean(img)) if fin.any() else 0.0
    d = np.where(fin, img, fill)
    base = float(np.nanmin(img)) if fin.any() else 0.0
    d = np.clip(d - base, 0, None) + eps
    est = np.full_like(d, d.mean())
    psf_m = psf[::-1, ::-1]
    n = int(iterations)
    for i in range(n):
        if progress:
            progress(i, n, f"deconvolving: iteration {i + 1}/{n}")
        conv = ndimage.convolve(est, psf, mode="reflect") + eps
        est = est * ndimage.convolve(d / conv, psf_m, mode="reflect")
    out = est + base - eps
    out[~fin] = np.nan
    return out


def _pad_psf(psf: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    out = np.zeros(shape)
    h, w = psf.shape
    cy, cx = shape[0] // 2, shape[1] // 2
    y0, x0 = cy - h // 2, cx - w // 2
    ys, xs = slice(max(y0, 0), min(y0 + h, shape[0])), slice(max(x0, 0), min(x0 + w, shape[1]))
    out[ys, xs] = psf[ys.start - y0: ys.stop - y0, xs.start - x0: xs.stop - x0]
    return out


def wiener(img: np.ndarray, psf: np.ndarray, noise_to_signal: float = 0.01) -> np.ndarray:
    """Frequency-domain Wiener filter with a constant noise-to-signal ratio."""
    img = np.asarray(img, float)
    fin = np.isfinite(img)
    fill = float(np.nanmean(img)) if fin.any() else 0.0
    d = np.where(fin, img, fill)
    H = np.fft.fft2(np.fft.ifftshift(_pad_psf(psf, d.shape)))
    D = np.fft.fft2(d)
    G = np.conj(H) / (np.abs(H) ** 2 + noise_to_signal)
    out = np.real(np.fft.ifft2(D * G))
    out[~fin] = np.nan
    return out


def deconvolve_grid(img: np.ndarray, fwhm_um: float, pitch_mm: float, method: str = "lucy", iterations: int = 20,
                    noise_to_signal: float = 0.01, progress=None) -> np.ndarray:
    """Deconvolve a gridded map; the PSF FWHM is given in µm, the grid pitch in mm."""
    fwhm_px = (fwhm_um / 1000.0) / max(pitch_mm, 1e-9)
    psf = gaussian_psf(fwhm_px)
    if method == "lucy":
        return richardson_lucy(img, psf, iterations, progress=progress)
    return wiener(img, psf, noise_to_signal)


def deconvolve_cost(shape: tuple[int, int], fwhm_um: float, pitch_mm: float, method: str = "lucy",
                    iterations: int = 20) -> float:
    """Rough number of multiply-adds of deconvolve_grid on an image of `shape`: the Maps perspective runs a cheap one
    at once and a costly one as a background job."""
    n = float(shape[0]) * float(shape[1])
    if method != "lucy":
        return n * max(np.log2(max(n, 2.0)), 1.0) * 10.0          # three FFTs of the image and their products
    k = gaussian_psf((fwhm_um / 1000.0) / max(pitch_mm, 1e-9)).size
    return n * k * 2.0 * max(int(iterations), 1)
