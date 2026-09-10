"""Shared application icon for the PySide6 windows.

Both GUIs (the control dashboard in :mod:`gui` and the waveform viewer in
:mod:`waveform`) use the same ``assets/icon.svg`` as their window/taskbar icon.
The SVG is rasterized at a few standard sizes with ``QSvgRenderer`` (always
available with PySide6) into a multi-resolution :class:`QIcon`, so no PNG export
step is needed and the icon stays crisp from the title bar up to the task
switcher. All Qt imports are local so importing this module never forces a Qt
dependency onto non-GUI code paths.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: The tracked source-of-truth icon, resolved relative to this package so it
#: works both from a source checkout and an installed wheel.
ICON_PATH = Path(__file__).resolve().parent / "assets" / "icon.svg"

#: A rasterized copy of the icon, shipped alongside the SVG. Linux desktop
#: environments (and WSLg's compositor) reference a PNG by name from a
#: ``.desktop`` entry rather than reading ``windowIcon``; the SVG alone does not
#: reach that taskbar surface. Regenerate with :func:`export_icon_png`.
ICON_PNG_PATH = Path(__file__).resolve().parent / "assets" / "icon.png"

#: Explicit Windows AppUserModelID. The taskbar groups and icons a process by
#: this id, not by ``QApplication.windowIcon``; without it a Python-launched Qt
#: app inherits the host launcher's identity (and shows its generic icon in the
#: taskbar). One stable id per app family keeps the two GUIs grouped sensibly.
WINDOWS_APP_ID = "NML.ScienceXYZ.StatefulDecodeAndSync"

#: Reverse-DNS base for the Linux/WSLg desktop entries and icon files. Each GUI
#: appends its own leaf (see :func:`install_desktop_entry`) so the two windows
#: get distinct ``.desktop`` files and taskbar identities.
LINUX_APP_ID_BASE = "org.nml.sciencexyz"

# Sizes cover common title-bar, taskbar, and task-switcher resolutions; Qt
# picks the nearest for each surface.
_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def load_app_icon():
    """Return a multi-resolution ``QIcon`` for ``assets/icon.svg``.

    Returns an empty ``QIcon`` (never raises) if the asset is missing or fails
    to render, so a packaging slip degrades to the default window icon rather
    than crashing the GUI. Requires a live ``QApplication``; call it after one
    exists.
    """
    from PySide6.QtGui import QIcon, QPainter, QPixmap
    from PySide6.QtCore import Qt

    icon = QIcon()
    if not ICON_PATH.is_file():
        return icon

    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(str(ICON_PATH))
    if not renderer.isValid():
        return icon

    for size in _ICON_SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        try:
            renderer.render(painter)
        finally:
            painter.end()
        icon.addPixmap(pixmap)
    return icon


def set_windows_app_id(app_id: str = WINDOWS_APP_ID) -> None:
    """Give this process an explicit Windows AppUserModelID (no-op elsewhere).

    On Windows the taskbar shows the icon for the process's AppUserModelID, not
    the window icon; a Python/Qt app otherwise inherits the launcher's id and
    its generic icon (the "penguin"/Python icon). Setting a stable id makes the
    taskbar adopt the window icon set by :func:`apply_app_icon`.

    Must run before the first window is shown. Best-effort: any failure (old
    Windows, missing API) is swallowed so it never blocks GUI startup.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def export_icon_png(size: int = 256) -> bool:
    """Rasterize ``assets/icon.svg`` to ``assets/icon.png``; return success.

    The tracked PNG is committed, so this is a maintenance helper for
    regenerating it after the SVG changes rather than a startup step. Requires a
    live ``QApplication``.
    """
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtCore import Qt

    if not ICON_PATH.is_file():
        return False
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(str(ICON_PATH))
    if not renderer.isValid():
        return False
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    return bool(pixmap.save(str(ICON_PNG_PATH), "PNG"))


def install_desktop_entry(app_leaf: str, display_name: str) -> str | None:
    """Install a Linux/WSLg ``.desktop`` entry + icon and return the app id.

    On Linux (including WSL/WSLg) the taskbar icon and grouping come from a
    ``.desktop`` file whose base name matches the window's ``WM_CLASS`` — set by
    Qt from :meth:`QGuiApplication.setDesktopFileName` — not from
    ``windowIcon``. This copies the shipped PNG into the user icon theme and
    writes a matching ``~/.local/share/applications/<app_id>.desktop`` so WSLg
    can associate the window with the icon.

    ``app_leaf`` is the per-GUI leaf appended to :data:`LINUX_APP_ID_BASE` (e.g.
    ``"waveform"``). Returns the full app id (the value to pass to
    :func:`set_desktop_file_name`) or ``None`` off Linux or on failure. It never
    raises: a read-only or unusual HOME degrades to no desktop entry.
    """
    if not sys.platform.startswith("linux"):
        return None
    app_id = f"{LINUX_APP_ID_BASE}.{app_leaf}"
    try:
        import os

        data_home = Path(
            os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
        )
        # Install the icon under the hicolor theme so lookups by app id resolve.
        icon_dir = data_home / "icons" / "hicolor" / "256x256" / "apps"
        icon_dir.mkdir(parents=True, exist_ok=True)
        icon_dest = icon_dir / f"{app_id}.png"
        if ICON_PNG_PATH.is_file():
            icon_dest.write_bytes(ICON_PNG_PATH.read_bytes())
            icon_ref = app_id  # themed lookup by name
        else:
            icon_ref = str(ICON_PATH)  # last resort: absolute SVG path

        apps_dir = data_home / "applications"
        apps_dir.mkdir(parents=True, exist_ok=True)
        # StartupWMClass must equal the WM_CLASS Qt sets from the desktop file
        # name (the app id), so the running window matches this entry.
        desktop = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={display_name}\n"
            f"Icon={icon_ref}\n"
            "Terminal=false\n"
            f"StartupWMClass={app_id}\n"
        )
        (apps_dir / f"{app_id}.desktop").write_text(desktop, encoding="utf-8")
        return app_id
    except Exception:
        return None


def set_desktop_file_name(app_id: str) -> None:
    """Point Qt at the installed ``.desktop`` entry (Linux/WSLg taskbar icon).

    Qt derives the Wayland/X ``WM_CLASS`` from this, which is how WSLg matches a
    window to its ``.desktop`` icon. No-op if there is no ``QApplication`` yet.
    """
    try:
        from PySide6.QtGui import QGuiApplication

        app = QGuiApplication.instance()
        if app is not None:
            app.setDesktopFileName(app_id)
    except Exception:
        pass


def setup_taskbar_identity(app_leaf: str, display_name: str) -> None:
    """Apply the platform-appropriate taskbar identity for one GUI.

    Windows: an explicit AppUserModelID. Linux/WSLg: install a ``.desktop``
    entry + PNG and point Qt at it. Call once per process, before the first
    window is shown, with a ``QApplication`` already created (needed on Linux).
    """
    set_windows_app_id()
    app_id = install_desktop_entry(app_leaf, display_name)
    if app_id is not None:
        set_desktop_file_name(app_id)


def apply_app_icon(target) -> None:
    """Set the shared icon on a ``QApplication`` or a ``QWidget``/window.

    A no-op when the icon fails to load. Applying it to both the
    ``QApplication`` (for the taskbar) and each window (for the title bar) gives
    the icon on every surface.
    """
    icon = load_app_icon()
    if not icon.isNull():
        target.setWindowIcon(icon)
