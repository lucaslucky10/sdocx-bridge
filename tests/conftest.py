import json
import pytest
from sdocx_bridge.library import Library
from sdocx_bridge.handwriting import import_profile


@pytest.fixture
def lib(tmp_path):
    library = Library(tmp_path / "library")
    glyphs = []
    for ch in "AB가나123=+":
        glyphs.append(
            {
                "ch": ch,
                "src": "synthetic-test",
                "id": ch,
                "adv": 0.7,
                "top": -1.0,
                "bot": 0.0,
                "strokes": [
                    {"p": [[0, 0], [0.35, -1], [0.7, 0]], "w": 0.06, "c": [20, 30, 40]},
                    {"p": [[0.1, -0.3], [0.6, -0.3]], "w": 0.06, "c": [20, 30, 40]},
                ],
            }
        )
    data = {
        "glyphs": glyphs,
        "stats": {"gap": 0.19, "space": 0.49, "pen": 0.06},
        "runs": [],
    }
    source = tmp_path / "profile.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    import_profile(library, source)
    return library
