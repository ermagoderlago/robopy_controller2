
# -*- coding: utf-8 -*-
"""
Hybrid Feature Matcher (FLANN + BF fallback) per SuperPoint.
- FLANN KDTREE (L2) con Lowe Ratio Test
- Fallback a BF L2 CrossCheck
- Filtri su distanza dinamica (media + std)
- Ordinamento per qualità del match
- Nessuna dipendenza da ROS -> testabile
"""

from dataclasses import dataclass
from typing import Dict, Tuple, List
import numpy as np
import cv2

@dataclass
class MatcherConfig:
    use_flann: bool = True
    ratio_thresh: float = 0.75     # Lowe ratio test
    crosscheck: bool = False       # utile per fallback
    max_matches: int = 200         # limite superiore
    dist_max_factor: float = 1.5   # filtra fuori match troppo lontani
    min_matches_for_valid: int = 10

class HybridMatcher:
    def __init__(self, cfg: Dict):
        self.cfg = MatcherConfig(**cfg) if not isinstance(cfg, MatcherConfig) else cfg

        # configurazione FLANN per descrittori float32 (SuperPoint)
        index_params = dict(algorithm=1, trees=5)   # 1 = FLANN_INDEX_KDTREE
        search_params = dict(checks=50)
        self.flann = cv2.FlannBasedMatcher(index_params, search_params)

        self.bf = cv2.BFMatcher(cv2.NORM_L2, crossCheck=self.cfg.crosscheck)

    def match(
        self,
        desc_prev: np.ndarray,
        desc_curr: np.ndarray
    ) -> List[cv2.DMatch]:
        """
        desc_prev: (N1, D) float32
        desc_curr: (N2, D) float32
        Ritorna lista di cv2.DMatch
        """
        if desc_prev is None or desc_curr is None:
            return []
        if len(desc_prev) == 0 or len(desc_curr) == 0:
            return []

        matches = []

        # ----- FLANN (default)
        if self.cfg.use_flann:
            try:
                knn = self.flann.knnMatch(desc_prev, desc_curr, k=2)
                for m_n in knn:
                    if len(m_n) < 2:
                        continue
                    m, n = m_n
                    if m.distance < self.cfg.ratio_thresh * n.distance:
                        matches.append(m)
            except Exception:
                pass  # se FLANN fallisce -> fallback

        # ----- Fallback BF
        if not matches:
            raw = self.bf.match(desc_prev, desc_curr)
            matches = sorted(raw, key=lambda x: x.distance)

        if not matches:
            return []

        # ----- Filtri di distanza dinamici
        dists = np.array([m.distance for m in matches], dtype=np.float32)
        mean_d = float(np.mean(dists))
        std_d  = float(np.std(dists))
        max_d  = mean_d + std_d * self.cfg.dist_max_factor

        matches = [m for m in matches if m.distance <= max_d]

        # ----- Ordina per qualità
        matches = sorted(matches, key=lambda x: x.distance)

        # ----- Limita a top-K
        if len(matches) > self.cfg.max_matches:
            matches = matches[:self.cfg.max_matches]

        return matches
