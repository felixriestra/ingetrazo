"""A raster view resized on the sheet re-renders by itself when Auto-render
is on (#80, @pacaeiro: «Each time I redimension the View I have to Update
the View, it's a pain»)."""
from __future__ import annotations

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def test_a_resized_view_renders_again_at_its_new_size(monkeypatch):
    from core.composition import RENDER_DPI
    from views.composer import ComposerWindow
    from views.main_window import MainWindow

    renders = []

    def fake_render(self, frame):
        w, h = frame.render_px(RENDER_DPI)
        self.render_cache[id(frame)] = QImage(w, h, QImage.Format_RGB32)
        self._stale.discard(id(frame))
        renders.append((w, h))

    monkeypatch.setattr(ComposerWindow, "render_frame", fake_render)
    win = MainWindow()
    comp = ComposerWindow(win)
    comp.show()
    try:
        comp._set_auto_render(True)
        frame = comp.comp.frames[0]
        frame.style = "style:Monochrome"
        comp._auto_render_stale()
        assert not comp._render_outgrown(frame)
        renders.clear()
        frame.w_mm *= 1.5                    # the corner handle, released
        comp._rebuild_after_change()
        assert comp._auto_timer.isActive()
        comp._auto_render_stale()
        assert renders == [frame.render_px(RENDER_DPI)]
        assert not comp._render_outgrown(frame)
    finally:
        comp.close()
        win._saved_version = win.viewport.scene.version
        win.close()


def test_a_released_resize_does_not_render_on_the_spot(monkeypatch):
    """#80, second half: the finished drag marks the frame stale and hands
    the render to the 400 ms auto timer, so the mouse release never blocks
    on the 300 dpi pass."""
    from views.composer import ComposerWindow, FrameItem
    from views.main_window import MainWindow

    renders = []
    monkeypatch.setattr(ComposerWindow, "render_frame",
                        lambda self, frame: renders.append(frame))
    win = MainWindow()
    comp = ComposerWindow(win)
    comp.show()
    try:
        comp._set_auto_render(True)
        comp._stale.clear()
        renders.clear()
        item = next(it for it in comp.canvas.items()
                    if isinstance(it, FrameItem))
        assert not comp.is_stale(item.model)
        comp._on_view_resized(item)
        assert comp.is_stale(item.model)         # the badge asks for it
        assert comp._auto_timer.isActive()       # ... and the pass is queued
        assert renders == []                     # nothing on the release
    finally:
        comp.close()
        win._saved_version = win.viewport.scene.version
        win.close()
