"""Mods page: install packages, switch features on, and configure them.

This is the launcher's replacement for the framework's own mod manager, which
is an ImGui panel compiled out by ``PSX_RECOMP_UI OFF``. It reads the installed
manifests and writes ``mods/state.toml`` - the file the runtime reads at boot.

Two things shape the design:

* **The runtime rewrites state.toml on every launch.** So this page re-reads on
  every show and after every session rather than trusting what it last wrote.
* **The runtime refuses to launch on an unresolvable selection**, printing the
  reason and exiting 1. We cannot run its byte-level resolver here, so we make
  the cheap manifest-level checks ourselves and show them before the player
  finds out the hard way.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import mods
from ..paths import Layout
from .common import card, dim, heading, row, section, set_status, warn
from .dialogs import confirm, tell
from .theme import PAGE_MARGINS, SPACE_2, SPACE_4


class ModsPage(QWidget):
    """One scrolling column: a banner, a card per package, an install button."""

    changed = Signal()

    def __init__(self, layout_: Layout, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.layout_ = layout_
        self._loading = True
        self.packages: list[mods.Package] = []
        self.state = mods.State()
        self._scan_problems: list[str] = []
        # Rebuilt wholesale on every reload, alongside the widgets they index.
        # (package, feature, toggle, dependent widgets) and
        # (package_id, feature_id, option_id) -> widget.
        self._groups: list[tuple] = []
        self._option_widgets: dict[tuple[str, str, str], QWidget] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)

        self._body = QWidget()
        self._lay = QVBoxLayout(self._body)
        self._lay.setContentsMargins(*PAGE_MARGINS)
        self._lay.setSpacing(SPACE_4)
        scroll.setWidget(self._body)

        self._loading = False
        self.reload()

    # -- lifecycle ---------------------------------------------------------
    def set_layout(self, layout_: Layout) -> None:
        """Called after a build, when the mods directory changes identity.

        Before the first build `runtime_exe` is only a predicted path, so
        `layout.mods` points somewhere that does not exist yet.
        """
        self.layout_ = layout_
        self.reload()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # The runtime normalises state.toml on every launch, so anything this
        # page cached before the game ran is potentially stale.
        super().showEvent(event)
        if not self._loading:
            self.reload()

    def reload(self) -> None:
        """Re-read manifests and state from disk, then rebuild the page."""
        self.packages, self._scan_problems = mods.scan(self.layout_)
        self.state = mods.load_state(self._state_path())
        self._rebuild()

    def _state_path(self) -> Path:
        return self.layout_.mods / "state.toml"

    # -- persistence -------------------------------------------------------
    def _save(self) -> None:
        if self._loading:
            return
        try:
            mods.save_state(self._state_path(), self.state, self.packages)
        except OSError as exc:
            tell(self, "Could not save",
                 "The mod selection could not be written.", detail=str(exc),
                 error=True)
            return
        self._refresh_problems()
        self.changed.emit()

    # -- construction ------------------------------------------------------
    def _clear(self) -> None:
        while self._lay.count():
            item = self._lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _rebuild(self) -> None:
        was_loading = self._loading
        self._loading = True
        self._clear()
        self._groups = []
        self._option_widgets = {}

        self._lay.addWidget(heading(
            "Mods",
            "Enhancements and add-ons. These change how the game runs, so if "
            "something misbehaves, switch the newest one off first."))

        self.problem_lbl = warn("")
        self.problem_lbl.setVisible(False)
        self._lay.addWidget(self.problem_lbl)

        if self._scan_problems:
            self._lay.addWidget(card(
                section("Packages that could not be read"),
                *[warn(text) for text in self._scan_problems],
                dim("The runtime rejects every mod when a package directory "
                    "disagrees with its manifest, so these are worth fixing "
                    "even if you do not want the package."),
                tone="warn",
            ))

        if not self.packages:
            self._lay.addWidget(card(
                section("Nothing installed"),
                dim("No mod packages were found in:"),
                dim(str(self.layout_.mod_packages)),
                dim("Built-in enhancements are installed automatically once "
                    "the game has been built."),
            ))
        else:
            for package in self.packages:
                widget = self._package_card(package)
                if widget is not None:
                    self._lay.addWidget(widget)

        self._lay.addWidget(self._install_card())
        self._lay.addStretch(1)

        self._loading = was_loading
        self._refresh_problems()
        self._sync_enabled()

    def _package_card(self, package: mods.Package) -> QWidget | None:
        rows: list[QWidget] = [section(package.name)]
        if package.description:
            rows.append(dim(package.description))

        rendered = 0
        for feature in package.features:
            # A legacy feature is the runtime's v1 migration shim; it rejects
            # being toggled, so showing a switch for it would be a lie.
            if feature.legacy:
                continue
            rows.extend(self._feature_rows(package, feature))
            rendered += 1

        if not rendered:
            return None

        remove = QPushButton("Remove")
        remove.clicked.connect(lambda _=False, p=package: self._remove(p))
        footer = QWidget()
        footer_lay = QHBoxLayout(footer)
        footer_lay.setContentsMargins(0, 0, 0, 0)
        footer_lay.addWidget(QLabel(f"Version {package.version}"))
        footer_lay.addStretch(1)
        footer_lay.addWidget(remove)
        rows.append(footer)

        return card(*rows)

    def _feature_rows(self, package: mods.Package,
                      feature: mods.Feature) -> list[QWidget]:
        rows: list[QWidget] = []

        toggle = QCheckBox(feature.name)
        toggle.setChecked(self.state.is_enabled(package, feature))
        toggle.toggled.connect(
            lambda on, p=package, f=feature: self._on_feature(p, f, on))
        rows.append(toggle)
        if feature.description:
            rows.append(dim(feature.description))

        # Everything below a feature toggle belongs to it, so it all greys out
        # together when the feature is off.
        dependents: list[QWidget] = []

        for option in feature.options:
            widget = self._option_widget(package, feature, option)
            if widget is None:
                continue
            labelled = row(option.label, widget)
            rows.append(labelled)
            dependents.append(labelled)
            if option.description:
                note = dim(option.description)
                rows.append(note)
                dependents.append(note)

        for resource in feature.resources:
            picker = self._resource_widget(package, feature, resource)
            labelled = row(resource.label, picker)
            rows.append(labelled)
            dependents.append(labelled)
            if resource.description:
                note = dim(resource.description)
                rows.append(note)
                dependents.append(note)

        self._groups.append((package, feature, toggle, dependents))
        return rows

    def _option_widget(self, package: mods.Package, feature: mods.Feature,
                       option: mods.Option) -> QWidget | None:
        current = self.state.value_of(package, feature, option)

        if option.type == mods.BOOLEAN:
            box = QCheckBox()
            box.setChecked(current == "true")
            box.toggled.connect(
                lambda on, p=package, f=feature, o=option:
                self._on_option(p, f, o, "true" if on else "false"))
            self._option_widgets[(package.id, feature.id, option.id)] = box
            return box

        if option.type == mods.CHOICE:
            combo = QComboBox()
            for choice in option.choices:
                combo.addItem(choice.label, choice.value)
            index = combo.findData(current)
            combo.setCurrentIndex(max(0, index))
            combo.currentIndexChanged.connect(
                lambda i, c=combo, p=package, f=feature, o=option:
                self._on_option(p, f, o, c.itemData(i)))
            self._option_widgets[(package.id, feature.id, option.id)] = combo
            return combo

        spin = QSpinBox()
        spin.setRange(option.min_value, option.max_value)
        spin.setSingleStep(option.step)
        parsed = mods.parse_canonical_int(current)
        spin.setValue(parsed if parsed is not None else option.min_value)
        spin.valueChanged.connect(
            lambda value, p=package, f=feature, o=option:
            self._on_option(p, f, o, str(value)))
        self._option_widgets[(package.id, feature.id, option.id)] = spin
        return spin

    def _resource_widget(self, package: mods.Package, feature: mods.Feature,
                         resource: mods.Resource) -> QWidget:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE_2)

        edit = QLineEdit(self.state.resource_of(package, feature, resource))
        edit.setReadOnly(True)
        edit.setPlaceholderText("Not set")
        browse = QPushButton("Browse...")
        browse.clicked.connect(
            lambda _=False, p=package, f=feature, r=resource, e=edit:
            self._browse_resource(p, f, r, e))
        clear = QPushButton("Clear")
        clear.clicked.connect(
            lambda _=False, p=package, f=feature, r=resource, e=edit:
            self._clear_resource(p, f, r, e))

        lay.addWidget(edit, 1)
        lay.addWidget(browse)
        lay.addWidget(clear)
        return box

    def _install_card(self) -> QWidget:
        button = QPushButton("Install a .psxmod...")
        button.setObjectName("Primary")
        button.clicked.connect(self._install)
        return card(
            section("Add a mod"),
            dim("Mod packages are .psxmod files. They are checked before "
                "anything is written - an archive with an unexpected layout "
                "is refused rather than half-installed."),
            button,
            dim(f"Installed to: {self.layout_.mod_packages}"),
        )

    # -- handlers ----------------------------------------------------------
    def _on_feature(self, package: mods.Package, feature: mods.Feature,
                    enabled: bool) -> None:
        self.state.get(package.id, feature.id).enabled = enabled
        self._sync_enabled()
        self._save()

    def _on_option(self, package: mods.Package, feature: mods.Feature,
                   option: mods.Option, value: str) -> None:
        if value is None or not option.validate(value):
            return
        self.state.get(package.id, feature.id).values[option.id] = value
        self._sync_enabled()
        self._save()

    def _browse_resource(self, package: mods.Package, feature: mods.Feature,
                         resource: mods.Resource, edit: QLineEdit) -> None:
        start = edit.text() or str(Path.home())
        if resource.wants_directory:
            chosen = QFileDialog.getExistingDirectory(
                self, resource.label, start)
        else:
            chosen, _ = QFileDialog.getOpenFileName(
                self, resource.label, start, resource.qt_filter())
        if not chosen:
            return
        # Mirror the runtime's own check: it rejects a path of the wrong kind
        # rather than failing later during resolution.
        path = Path(chosen)
        wrong = (resource.wants_directory and not path.is_dir()) or \
                (not resource.wants_directory and not path.is_file())
        if wrong:
            kind = "folder" if resource.wants_directory else "file"
            tell(self, "Wrong kind of path",
                 f"{resource.label} needs a {kind}.", error=True)
            return
        edit.setText(str(path))
        self.state.get(package.id, feature.id).resources[resource.id] = str(path)
        self._save()

    def _clear_resource(self, package: mods.Package, feature: mods.Feature,
                        resource: mods.Resource, edit: QLineEdit) -> None:
        edit.clear()
        self.state.get(package.id, feature.id).resources.pop(resource.id, None)
        self._save()

    def _install(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Install a mod package", str(Path.home()),
            "Mod packages (*.psxmod *.zip);;All files (*)")
        if not chosen:
            return
        try:
            package = mods.install_archive(self.layout_, Path(chosen))
        except mods.InstallError as exc:
            tell(self, "Could not install", "That package was refused.",
                 detail=str(exc), error=True)
            return
        except OSError as exc:
            tell(self, "Could not install",
                 "The package could not be written to disk.",
                 detail=str(exc), error=True)
            return
        self.reload()
        tell(self, "Installed",
             f"{package.name} {package.version} is installed. Switch it on "
             "above, then relaunch the game.")

    def _remove(self, package: mods.Package) -> None:
        if not confirm(self, "Remove this mod?",
                       f"{package.name} {package.version} will be deleted "
                       "from disk.", accept="Remove", danger=True):
            return
        try:
            mods.remove_package(self.layout_, package)
        except OSError as exc:
            tell(self, "Could not remove", "The package could not be deleted.",
                 detail=str(exc), error=True)
            return
        # Drop its selections too, so a reinstall starts from the manifest
        # defaults rather than inheriting whatever was set before.
        for key in [k for k in self.state.features if k[0] == package.id]:
            self.state.features.pop(key, None)
        self.state.versions.pop(package.id, None)
        self.reload()
        self._save()

    # -- state -------------------------------------------------------------
    def _sync_enabled(self) -> None:
        """Grey out what the runtime would ignore.

        Two independent reasons a control is inert: its feature is off, or
        another boolean option declares `disabled_by` over it. Greying rather
        than hiding keeps the layout steady and lets the player see what the
        setting would be.
        """
        for package, feature, toggle, dependents in self._groups:
            on = self.state.is_enabled(package, feature)
            toggle.setChecked(on)
            for widget in dependents:
                widget.setEnabled(on)
            for option in feature.options:
                if not option.disabled_by:
                    continue
                widget = self._option_widgets.get(
                    (package.id, feature.id, option.id))
                if widget is None:
                    continue
                controller = next(
                    (o for o in feature.options if o.id == option.disabled_by),
                    None)
                if controller is None:
                    continue
                overridden = self.state.value_of(
                    package, feature, controller) == "true"
                widget.setEnabled(on and not overridden)

    def _refresh_problems(self) -> None:
        if not hasattr(self, "problem_lbl"):
            return
        problems = mods.problems_for(self.packages, self.state)
        if problems:
            set_status(self.problem_lbl, "Warn",
                       "The game will refuse to start with this selection:\n"
                       + "\n".join("- " + p for p in problems))
        self.problem_lbl.setVisible(bool(problems))
