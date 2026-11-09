"""Re-implemented /drawing_detection and /pdf-to-images (previous-service contract)."""

import base64
import io
import shutil
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from patent_converter.main import create_app
from tests.conftest import make_settings

fitz = pytest.importorskip("fitz")


def make_pdf(pages: int = 2) -> tuple[bytes, list[bytes]]:
    doc = fitz.open()
    embedded = []
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), f"Page {i + 1}")
        buf = io.BytesIO()
        Image.new("RGB", (60 + i, 40), (i * 40, 100, 200)).save(buf, "PNG")
        page.insert_image(fitz.Rect(72, 100, 272, 240), stream=buf.getvalue())
    data = doc.tobytes()
    doc.close()
    check = fitz.open(stream=data, filetype="pdf")
    for page in check:
        for img in page.get_images(full=True):
            embedded.append(check.extract_image(img[0])["image"])
    check.close()
    return data, embedded


@pytest.fixture(scope="module")
def stand_in_model(tmp_path_factory):
    """Keras-2 .h5 model with the production input/output shape (224x224x3 -> 3 classes)."""
    tf_keras = pytest.importorskip("tf_keras")
    path = tmp_path_factory.mktemp("model") / "best_model.h5"
    inp = tf_keras.Input(shape=(224, 224, 3))
    x = tf_keras.layers.GlobalAveragePooling2D()(inp)
    out = tf_keras.layers.Dense(3, activation="softmax")(x)
    tf_keras.Model(inp, out).save(str(path))
    return path


def wait_model(client, timeout=120):
    model = client.app.state.app_state.drawing_model
    t0 = time.time()
    while model.loading and time.time() - t0 < timeout:
        time.sleep(0.2)
    return model


def test_pdf_to_images_contract(tmp_path):
    data, embedded = make_pdf(3)
    with TestClient(create_app(make_settings(tmp_path))) as c:
        r = c.post("/pdf-to-images", content=data, headers={"content-type": "application/pdf"})
    assert r.status_code == 200 and r.headers["content-type"] == "application/json; charset=utf-8"
    body = r.json()
    assert list(body) == ["success", "images"] and body["success"] is True
    assert [base64.b64decode(s) for s in body["images"]] == embedded and len(embedded) == 3


def test_pdf_routes_reject_non_pdf_like_before(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as c:
        r = c.post("/pdf-to-images", content=b"PK\x03\x04 not a pdf")
        assert r.status_code == 500 and r.json()["detail"] and r.json()["code"] == "INVALID_PDF"
        assert c.post("/pdf-to-images", content=b"").status_code == 400


def test_pdf_limits(tmp_path):
    data, _ = make_pdf(3)
    with TestClient(create_app(make_settings(tmp_path, pdf={"max_pages": 2}))) as c:
        r = c.post("/pdf-to-images", content=data)
    assert r.status_code == 500 and "limit is 2" in r.json()["detail"]


def test_drawing_detection_model_not_loaded(tmp_path):
    data, _ = make_pdf(1)
    with TestClient(create_app(make_settings(tmp_path, pdf={"model_path": str(tmp_path / "missing.h5")}))) as c:
        wait_model(c)
        assert c.get("/health").json()["model_loaded"] is False
        r = c.post("/drawing_detection", content=data)
    assert r.status_code == 500 and r.json()["detail"] == "Model not loaded"


@pytest.mark.skipif(not shutil.which("pdftoppm"), reason="poppler not installed")
def test_drawing_detection_contract(tmp_path, stand_in_model):
    data, _ = make_pdf(2)
    with TestClient(create_app(make_settings(tmp_path, pdf={"model_path": str(stand_in_model)}))) as c:
        model = wait_model(c)
        assert model.loaded, model.error
        assert c.get("/health").json()["model_loaded"] is True
        r = c.post("/drawing_detection", content=data)
    assert r.status_code == 200, r.text
    body = r.json()
    assert list(body) == ["results"]
    assert [x["page"] for x in body["results"]] == [1, 2]
    assert all(x["result"] in ("Valid drawing", "Not valid drawing") for x in body["results"])


def test_preprocessing_matches_previous_service(stand_in_model):
    """Same pipeline as AI_API_v2.process_image: RGB->BGR, resize 224, ResNet50 preprocess_input."""
    tf_keras = pytest.importorskip("tf_keras")
    import cv2
    import numpy as np

    from patent_converter.config.settings import PdfSettings
    from patent_converter.pdf.drawing_detection import CLASSES, DrawingModel

    m = DrawingModel(PdfSettings(model_path=stand_in_model))
    m._load()
    image = Image.new("RGB", (800, 1100), (250, 240, 230))
    legacy_model = tf_keras.models.load_model(str(stand_in_model))
    img = cv2.resize(cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR), (224, 224))
    pred = legacy_model.predict(tf_keras.applications.resnet50.preprocess_input(np.expand_dims(img, 0)), verbose=0)
    expected = CLASSES[int(np.argmax(pred))]
    expected = "Valid drawing" if expected in ("drawing", "flowchart") else "Not valid drawing"
    assert m.classify(image) == expected
