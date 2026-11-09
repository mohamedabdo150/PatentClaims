"""POST /drawing_detection: classify every PDF page as a drawing or not.

Re-implementation of the previous service with the **same model file and the same
preprocessing**, so predictions are identical:

    pdf2image (poppler, 200 dpi) -> PIL RGB -> cv2 RGB->BGR -> resize 224x224
    -> ResNet50 preprocess_input -> model.predict -> argmax over
    ['drawing', 'flowchart', 'not_valid'] -> "Valid drawing" | "Not valid drawing"

Differences that do not change results: pages are rasterized one at a time (bounded
memory), page count / page size / PDF size are limited, and predictions are serialized
with a lock and never run on the event loop.

TensorFlow is optional and loaded in a background thread at startup. The claims
service works without it; this route then answers "Model not loaded" as before.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from ..api.errors import AppError, ErrorCode
from ..config.settings import PdfSettings
from .common import open_pdf

log = logging.getLogger("patent_converter.pdf")
CLASSES = ["drawing", "flowchart", "not_valid"]


class DrawingModel:
    def __init__(self, cfg: PdfSettings):
        self.cfg = cfg
        self.model = None
        self.error: str | None = None
        self.loading = False
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self.model is not None

    def load_async(self) -> None:
        if not self.cfg.enabled:
            self.error = "disabled"
            return
        self.loading = True
        threading.Thread(target=self._load, name="model-loader", daemon=True).start()

    def _load(self) -> None:
        try:
            path = Path(self.cfg.model_path)
            if not path.exists():
                raise FileNotFoundError(f"model file not found: {path}")
            # The model was saved with Keras 2 (.h5); tf_keras keeps Keras 2 semantics on current TensorFlow.
            os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")
            import tensorflow as tf  # noqa: F401  (heavy import, only here)
            try:
                import tf_keras as keras
            except ImportError:  # pragma: no cover - TensorFlow versions bundling Keras 2
                from tensorflow import keras  # type: ignore[no-redef]
            self.model = keras.models.load_model(str(path))
            log.info("drawing model loaded", extra={"model": str(path)})
        except Exception as exc:  # noqa: BLE001 - reported by /health and the route
            self.error = f"{type(exc).__name__}: {exc}"[:300]
            log.error("drawing model not loaded", extra={"reason": self.error})
        finally:
            self.loading = False

    def classify(self, image) -> str:
        import cv2
        import numpy as np
        try:
            from tf_keras.applications.resnet50 import preprocess_input
        except ImportError:  # pragma: no cover
            from tensorflow.keras.applications.resnet50 import preprocess_input  # type: ignore[no-redef]

        img = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        img = cv2.resize(img, (224, 224))
        x = preprocess_input(np.expand_dims(img, axis=0))
        with self._lock:
            pred = self.model.predict(x, verbose=0)
        result = CLASSES[int(np.argmax(pred))]
        return "Valid drawing" if result in ("drawing", "flowchart") else "Not valid drawing"


def detect_drawings(data: bytes, model: DrawingModel, deadline_check=lambda: None) -> list[dict]:
    if not model.loaded:
        raise AppError(ErrorCode.MODEL_NOT_LOADED, "Model not loaded")
    cfg = model.cfg
    from pdf2image import convert_from_bytes

    doc = open_pdf(data, cfg)
    try:
        sizes = [(p.rect.width, p.rect.height) for p in doc]
    finally:
        doc.close()
    results = []
    for i, (w_pt, h_pt) in enumerate(sizes, start=1):
        deadline_check()
        pixels = (w_pt / 72 * cfg.dpi) * (h_pt / 72 * cfg.dpi)
        if pixels > cfg.max_page_pixels:
            raise AppError(ErrorCode.INVALID_PDF, f"page {i} is too large to rasterize ({int(pixels)} pixels)")
        pages = convert_from_bytes(data, dpi=cfg.dpi, first_page=i, last_page=i, poppler_path=cfg.poppler_path)
        if len(pages) != 1:
            raise AppError(ErrorCode.INVALID_PDF, f"page {i} could not be rasterized")
        results.append({"page": i, "result": model.classify(pages[0].convert("RGB"))})
        pages[0].close()
    return results
