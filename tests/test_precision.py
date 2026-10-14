import math
from sdocx_bridge.ink import sdocx_export


def test_long_stroke_quantization_does_not_accumulate(lib, tmp_path):
    points = [[20 + i * 0.037, 100 + 15 * math.sin(i * 0.013)] for i in range(1200)]
    s = {
        "id": "long",
        "points": points,
        "width": 1.5,
        "color": [0, 0, 0],
        "pressure": [0.1 + i * 0.0005 for i in range(1200)],
        "time": [i * 8 for i in range(1200)],
    }
    output, _ = sdocx_export(
        [{"width": 595, "height": 842, "strokes": [s]}], tmp_path / "export"
    )
    n = lib.import_file(output)
    got = lib.page(n["id"], 0)["strokes"][0]
    assert len(got["points"]) == len(points)
    assert (
        max(abs(a - b) for p, q in zip(points, got["points"]) for a, b in zip(p, q))
        <= 1 / 64 + 1e-6
    )
    assert (
        max(abs(a - b) for a, b in zip(s["pressure"], got["pressure"]))
        <= 1 / (64 * 128) + 1e-6
    )
    assert got["time"] == s["time"]
