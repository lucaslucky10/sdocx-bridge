import zipfile
import pytest
from sdocx_bridge.security import safe_extract, under, identifier


@pytest.mark.parametrize(
    "name",
    [
        "../evil",
        "/absolute",
        "C:/evil",
        "media\\evil",
        "media/CON.txt",
        "media/x.",
        "a/../../b",
        "NUL",
    ],
)
def test_archive_traversal(tmp_path, name):
    src = tmp_path / "bad.sdocx"
    with zipfile.ZipFile(src, "w") as z:
        info = zipfile.ZipInfo("entry")
        info.filename = name
        info.orig_filename = name
        z.writestr(info, b"bad")
    with pytest.raises(ValueError):
        safe_extract(src, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_case_collision(tmp_path):
    src = tmp_path / "bad.sdocx"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("media/X", b"a")
        z.writestr("media/x", b"b")
    with pytest.raises(ValueError):
        safe_extract(src, tmp_path / "out")


def test_symlink(tmp_path):
    src = tmp_path / "bad.sdocx"
    with zipfile.ZipFile(src, "w") as z:
        i = zipfile.ZipInfo("link")
        i.create_system = 3
        i.external_attr = 0o120777 << 16
        z.writestr(i, b"../../x")
    with pytest.raises(ValueError):
        safe_extract(src, tmp_path / "out")


def test_scoped_paths(tmp_path):
    with pytest.raises(ValueError):
        under(tmp_path, "../outside")
    with pytest.raises(ValueError):
        identifier("../outside")


def test_zip_bomb(tmp_path):
    p = tmp_path / "bomb.sdocx"
    with zipfile.ZipFile(p, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("large", b"0" * (2 * 1024**2))
    with pytest.raises(ValueError):
        safe_extract(p, tmp_path / "out")
