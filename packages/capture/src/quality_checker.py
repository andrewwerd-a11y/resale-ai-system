"""
QualityChecker — detects blur and exposure issues using OpenCV.
Laplacian variance measures sharpness; histogram clipping detects exposure problems.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class QualityResult:
    is_sharp: bool
    is_exposed: bool
    blur_score: float
    exposure_score: float
    recommendation: str   # "accept" | "acceptable" | "retake"
    reason: str


class QualityChecker:
    def __init__(self, blur_threshold: float = 100.0):
        self.blur_threshold = blur_threshold

    def check(self, image_path: Path) -> QualityResult:
        """
        Analyse a single image for quality.
        Returns QualityResult with scores and a recommendation.
        """
        try:
            import cv2
            import numpy as np

            img = cv2.imread(str(image_path))
            if img is None:
                return QualityResult(False, False, 0.0, 0.0, "retake", "Could not read image")

            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            # Sharpness via Laplacian variance
            blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            is_sharp = blur_score >= self.blur_threshold

            # Exposure via histogram clipping
            hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
            total = float(gray.size)
            black_clip = float(hist[0][0]) / total
            white_clip = float(hist[255][0]) / total
            exposure_score = 1.0 - black_clip - white_clip
            is_exposed = black_clip < 0.05 and white_clip < 0.05

            if is_sharp and is_exposed:
                rec = "accept"
                reason = f"Sharp (score: {blur_score:.0f}) and well-exposed"
            elif is_sharp and not is_exposed:
                rec = "acceptable"
                reason = (
                    f"Sharp but exposure issues "
                    f"(black: {black_clip:.1%}, white: {white_clip:.1%})"
                )
            elif not is_sharp and is_exposed:
                rec = "retake"
                reason = f"Blurry (score: {blur_score:.0f}, threshold: {self.blur_threshold:.0f})"
            else:
                rec = "retake"
                reason = f"Blurry and poor exposure"

            return QualityResult(is_sharp, is_exposed, blur_score, exposure_score, rec, reason)

        except Exception as e:
            logger.error("Quality check failed for %s: %s", image_path, e)
            return QualityResult(False, False, 0.0, 0.0, "retake", f"Error: {e}")
