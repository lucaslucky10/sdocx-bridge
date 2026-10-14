import pymupdf as fitz
from fastapi.testclient import TestClient
from sdocx_bridge.web import create_app


def test_upload_pdf_and_duplicate(lib):
    client = TestClient(create_app(lib.root, token="test"))
    headers = {"x-bridge-token": "test"}
    with fitz.open() as pdf:
        pdf.new_page()
        data = pdf.tobytes()
    response = client.post(
        "/api/upload",
        headers=headers,
        files={"file": ("sample.pdf", data, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    again = client.post(
        "/api/upload",
        headers=headers,
        files={"file": ("again.pdf", data, "application/pdf")},
    )
    assert again.json()["id"] == result["id"]
    preview = client.get(f"/api/notes/{result['id']}/pages/0/preview", headers=headers)
    assert preview.content.startswith(b"\x89PNG")
    bad = client.post(
        "/api/upload",
        headers=headers,
        files={"file": ("bad.sdocx", b"not-a-zip", "application/octet-stream")},
    )
    assert bad.status_code == 400


def test_profile_api_and_xss_name(lib):
    client = TestClient(create_app(lib.root, token="test"))
    headers = {"x-bridge-token": "test"}
    data = (lib.root / "profiles/my-handwriting.json").read_bytes()
    response = client.post(
        "/api/upload?profile_name=second",
        headers=headers,
        files={"file": ("profile.json", data, "application/json")},
    )
    assert response.status_code == 200
    bad = client.post(
        "/api/upload?profile_name=../../outside",
        headers=headers,
        files={"file": ("profile.json", data, "application/json")},
    )
    assert bad.status_code == 400
