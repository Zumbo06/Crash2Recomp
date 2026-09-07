"""Message dialogs.

The launcher had none of these: destructive actions happened on a single
click, and failures were reported by turning a small grey label red. Anything
that discards work, takes minutes, or has already gone wrong deserves a real
dialog the user cannot miss.

Everything here is deliberately plain: a title, one sentence saying what will
happen, and buttons whose labels name the action rather than "OK". A player
should never have to work out what "OK" agrees to.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget


def confirm(parent: QWidget | None, title: str, body: str,
            accept: str = "Continue", *, danger: bool = False) -> bool:
    """Ask before doing something the user cannot easily undo.

    ``danger`` only changes the icon; the default button is Cancel either way,
    so Return never triggers the destructive branch by accident.
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning if danger
                else QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(title)
    box.setInformativeText(body)
    go = box.addButton(accept, QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.exec()
    return box.clickedButton() is go


def about(parent: QWidget | None) -> None:
    """Version, what this is, and the legal position.

    The last part is not decoration. This project is licensed for
    non-commercial use only, redistributes a third-party BIOS under MIT, and
    is a fan work using someone else's trademark - a user is entitled to see
    all three without reading the source.
    """
    from ..version import DISCLAIMER, full_version

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle("About")
    box.setText("Crash Bandicoot 2 Recompiled")
    box.setInformativeText(
        "Version %s\n\n"
        "The PlayStation game translated to native code and run directly, "
        "rather than emulated.\n\n"
        "%s" % (full_version(), DISCLAIMER)
    )
    box.setDetailedText(
        "Licences\n"
        "--------\n"
        "psxrecomp (the recompiler and runtime): PolyForm Noncommercial 1.0.0.\n"
        "    Free to use and share for any non-commercial purpose. Selling it, "
        "or bundling it with anything commercial, is not permitted.\n\n"
        "OpenBIOS (PCSX-Redux): MIT. Shipped as bios/openbios.bin with its "
        "notice in bios/OpenBIOS.LICENSE.\n\n"
        "SDL3: zlib licence.\n\n"
        "Qt / PySide6: LGPL v3. The Qt libraries are shipped as separate files "
        "and may be replaced.\n\n"
        "Full texts are in the LICENSES folder next to this launcher."
    )
    box.exec()


def tell(parent: QWidget | None, title: str, body: str,
         *, detail: str = "", error: bool = False) -> None:
    """Report something that already happened. ``detail`` goes behind Show
    Details, so a stack trace or an exit code never buries the sentence that
    explains it."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical if error
                else QMessageBox.Icon.Information)
    box.setWindowTitle(title)
    box.setText(title)
    box.setInformativeText(body)
    if detail:
        box.setDetailedText(detail)
    box.exec()
