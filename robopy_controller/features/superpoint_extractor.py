
# -*- coding: utf-8 -*-
"""
SuperPoint feature extractor (modulare)
- Decodifica heatmap (stride=8) -> keypoints + scores
- NMS spaziale
- Grid filtering per distribuzione uniforme
- Bilinear sampling dei descrittori
- Filtri bordo e top-K
Autore: Luca + Copilot (modulo per refactor)
"""

from dataclasses import dataclass
from typing import Tuple, Optional, Dict
import numpy as np
import cv2

@dataclass
class SuperPointConfig:
    det_thresh: float = 0.010          # soglia minima su score normalizzato (0..1)
    nms_radius: int = 4                # raggio NMS in pixel (sul full-res)
    max_keypoints: int = 1000          # limite superiore di KP
    grid_rows: int = 0                 # 0 = disabilitato; altrimenti griglia RxC
    grid_cols: int = 0
    max_per_cell: int = 1              # kp max per cella con grid filter
    border: int = 8                    # margine minimo dal bordo (pixel)
    descriptor_dim: int = 256          # normalmente 256
    stride: int = 8                    # stride SuperPoint
    # Normalizzazione descrittori
    l2_normalize: bool = True

class SuperPointExtractor:
    def __init__(self, cfg: Dict):
        # Consente passaggio diretto di dict YAML -> dataclass
        self.cfg = SuperPointConfig(**cfg) if not isinstance(cfg, SuperPointConfig) else cfg

    def extract(
        self,
        score_map: np.ndarray,
        desc_map: np.ndarray,
        image_gray: Optional[np.ndarray] = None
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        score_map:  (Hc, Wc) or (1, Hc, Wc)  scores in [0..1] o non normalizzati
        desc_map:   (D, Hc, Wc)
        image_gray: (H, W) uint8/float, opzionale (per assert, border)
        Ritorna:
            kpts_xy: (N, 2) float32  -> (x, y) in coordinate immagine
            desc:    (N, D) float32  L2-normalized se cfg.l2_normalize
            scores:  (N,)  float32
        """
        # --- sanitize shapes
        if score_map.ndim == 3 and score_map.shape[0] == 1:
            score_map = score_map[0]
        assert score_map.ndim == 2, f"score_map shape non valido: {score_map.shape}"
        D, Hc, Wc = desc_map.shape
        assert D == self.cfg.descriptor_dim, f"desc dim {D} != {self.cfg.descriptor_dim}"
        assert score_map.shape == (Hc, Wc), f"score_map {score_map.shape} != desc_map (Hc,Wc) {(Hc,Wc)}"

        H = Hc * self.cfg.stride
        W = Wc * self.cfg.stride
        if image_gray is not None:
            assert image_gray.shape[:2] == (H, W), f"image_gray shape {image_gray.shape} != {(H,W)}"

        # --- thresholding preliminare (su cella)
        scores_cell = score_map.astype(np.float32).copy()
        mask_thresh = scores_cell >= self.cfg.det_thresh
        if not np.any(mask_thresh):
            return np.zeros((0,2), np.float32), np.zeros((0, D), np.float32), np.zeros((0,), np.float32)

        ys_c, xs_c = np.where(mask_thresh)
        cand_scores = scores_cell[ys_c, xs_c]

        # --- proiezione al full-res (centro della cella)
        xs = (xs_c * self.cfg.stride + self.cfg.stride / 2.0).astype(np.float32)
        ys = (ys_c * self.cfg.stride + self.cfg.stride / 2.0).astype(np.float32)

        # --- border filter
        if self.cfg.border > 0:
            keep = (xs >= self.cfg.border) & (xs < (W - self.cfg.border)) & \
                   (ys >= self.cfg.border) & (ys < (H - self.cfg.border))
            xs, ys, cand_scores = xs[keep], ys[keep], cand_scores[keep]
            if xs.size == 0:
                return (np.zeros((0,2), np.float32),
                        np.zeros((0, D), np.float32),
                        np.zeros((0,),  np.float32))

        # --- NMS su full-res: implementazione rapida con griglia/bucket
        # Ordina per score discendente
        order = np.argsort(-cand_scores)
        xs, ys, cand_scores = xs[order], ys[order], cand_scores[order]

        # Crea una occupancy map a risoluzione ridotta per NMS
        r = int(self.cfg.nms_radius)
        taken = np.zeros((H, W), dtype=np.uint8) if r == 0 else None
        keep_idx = []
        if r <= 0:
            keep_idx = list(range(xs.size))
        else:
            # Per efficienza usiamo una mappa a passi r (dilatazioni logiche emulate)
            # Qui scegliamo un approccio pragmatico: applichiamo un mask circolare locale
            # usando slicing per evitare dilatazioni costose per N punti.
            radius_sq = r * r
            selected = []
            for i in range(xs.size):
                xi, yi = int(round(xs[i])), int(round(ys[i]))
                if xi < 0 or yi < 0 or xi >= W or yi >= H:
                    continue
                # Verifica se troppo vicino a un kp già selezionato
                too_close = False
                for (xj, yj) in selected:
                    dx = xi - xj
                    dy = yi - yj
                    if dx*dx + dy*dy <= radius_sq:
                        too_close = True
                        break
                if not too_close:
                    selected.append((xi, yi))
                    keep_idx.append(i)

        xs, ys, cand_scores = xs[keep_idx], ys[keep_idx], cand_scores[keep_idx]

        # --- Grid filter (opzionale) per distribuzione uniforme
        if self.cfg.grid_rows and self.cfg.grid_cols:
            xs, ys, cand_scores = self._grid_filter(xs, ys, cand_scores, H, W)

        # --- Top-K
        if xs.size > self.cfg.max_keypoints:
            xs, ys, cand_scores = xs[:self.cfg.max_keypoints], ys[:self.cfg.max_keypoints], cand_scores[:self.cfg.max_keypoints]

        if xs.size == 0:
            return (np.zeros((0,2), np.float32),
                    np.zeros((0, D), np.float32),
                    np.zeros((0,),  np.float32))

        # --- Bilinear sampling dei descrittori a partire da desc_map (D, Hc, Wc)
        # Coordinate in spazio cella:
        xs_cell = xs / self.cfg.stride - 0.5
        ys_cell = ys / self.cfg.stride - 0.5
        desc = self._bilinear_sample_desc(desc_map, xs_cell, ys_cell)  # (N, D)

        # Normalizzazione L2 dei descrittori
        if self.cfg.l2_normalize and desc.shape[0] > 0:
            norms = np.linalg.norm(desc, axis=1, keepdims=True) + 1e-8
            desc = desc / norms

        kpts_xy = np.stack([xs, ys], axis=1).astype(np.float32)
        scores = cand_scores.astype(np.float32)
        return kpts_xy, desc.astype(np.float32), scores

    # ------------------------ helpers ------------------------

    def _grid_filter(self, xs, ys, scores, H, W):
        """Mantiene al massimo max_per_cell per ogni cella di una griglia RxC."""
        R, C, K = self.cfg.grid_rows, self.cfg.grid_cols, self.cfg.max_per_cell
        cell_h = H / R
        cell_w = W / C
        # ordina già per score discendente (già fatto a monte)
        kept = []
        counters = {}
        for i in range(xs.size):
            cx = int(xs[i] // cell_w)
            cy = int(ys[i] // cell_h)
            cx = min(max(cx, 0), C-1)
            cy = min(max(cy, 0), R-1)
            key = (cy, cx)
            count = counters.get(key, 0)
            if count < K:
                kept.append(i)
                counters[key] = count + 1
        return xs[kept], ys[kept], scores[kept]

    def _bilinear_sample_desc(self, desc_map: np.ndarray, xs_c: np.ndarray, ys_c: np.ndarray) -> np.ndarray:
        """
        desc_map: (D, Hc, Wc)
        xs_c, ys_c: coordinate in spazio cella (float), già centrate
        Ritorna: (N, D)
        """
        D, Hc, Wc = desc_map.shape
        N = xs_c.shape[0]
        # clamp ai bordi della grid cell
        xs_c = np.clip(xs_c, 0.0, Wc - 1.001)
        ys_c = np.clip(ys_c, 0.0, Hc - 1.001)

        x0 = np.floor(xs_c).astype(np.int32)
        y0 = np.floor(ys_c).astype(np.int32)
        x1 = x0 + 1
        y1 = y0 + 1
        x1 = np.clip(x1, 0, Wc - 1)
        y1 = np.clip(y1, 0, Hc - 1)

        wa = (x1 - xs_c) * (y1 - ys_c)
        wb = (xs_c - x0) * (y1 - ys_c)
        wc = (x1 - xs_c) * (ys_c - y0)
        wd = (xs_c - x0) * (ys_c - y0)

        # Preleva i 4 vicini
        # desc_map: (D, Hc, Wc)
        Ia = desc_map[:, y0, x0]  # (D, N) indicizzazione vettoriale
        Ib = desc_map[:, y0, x1]
        Ic = desc_map[:, y1, x0]
        Id = desc_map[:, y1, x1]

        # Pesa e trasponi a (N, D)
        desc = (Ia * wa + Ib * wb + Ic * wc + Id * wd).T  # (N, D)
        return desc.astype(np.float32)
