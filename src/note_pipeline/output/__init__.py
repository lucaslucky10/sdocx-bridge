"""Bridge modification: lazy imports avoid starting plotting backends in MCP."""
from .base import ExportOptions, NoteExporter

def __getattr__(name):
    if name == "SamsungNotesExporter":
        from .samsung_notes import SamsungNotesExporter
        return SamsungNotesExporter
    if name == "ExcalidrawExporter":
        from .excalidraw import ExcalidrawExporter
        return ExcalidrawExporter
    if name == "PngExporter":
        from .png import PngExporter
        return PngExporter
    if name == "get_exporter_class":
        from .registry import get_exporter_class
        return get_exporter_class
    raise AttributeError(name)
