"""Mod packages: scan manifests, read and write ``mods/state.toml``.

The runtime has a complete mod system compiled in, but the framework's own
manager is an ImGui panel that this project compiles out (``PSX_RECOMP_UI
OFF``). So the launcher talks to the runtime through the one thing they both
understand: the state file.

Division of labour
------------------
``manifest.toml`` is **read-only, runtime-owned** content describing what a
package offers. ``state.toml`` is **user state** describing what is switched
on. We parse the first and own the second.

Round-tripping
--------------
The runtime rewrites ``state.toml`` from its in-memory model on *every* launch
(``mod_runtime_commit`` -> ``save_state``). Comments, key order and any
formatting are normalised away, and unknown top-level keys are dropped. So this
module does not try to own a formatted file - it re-reads after every session
and writes a plain canonical form.

Values are written as **strings regardless of declared type**, which is what
the runtime's own writer emits (its selection maps are
``map<string, string>``). Its reader is more tolerant - it accepts bools and
integers too - but matching the writer keeps a launcher-written file
byte-identical to a runtime-written one.

Why hand-rolled TOML
--------------------
Python ships a reader (``tomllib``) and no writer. ``gametoml.py`` and
``usersettings.py`` already set the precedent of emitting by hand rather than
taking a dependency for a handful of scalars, and this file is the easiest
possible case: strings, bools, and a fixed table shape.
"""

from __future__ import annotations

import os
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

STATE_FORMAT_VERSION = 2

# Manifest format versions this launcher understands. The runtime accepts 1-5;
# we render 2-5 the same way and treat a v1 (feature-less) manifest as legacy.
MAX_FORMAT_VERSION = 5

BOOLEAN, CHOICE, INTEGER = "boolean", "choice", "integer"


# --------------------------------------------------------------------------
# Manifest model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Choice:
    value: str
    label: str


@dataclass
class Option:
    feature: str
    id: str
    label: str
    description: str = ""
    group: str = "General"
    type: str = BOOLEAN
    default: str = "false"
    choices: list[Choice] = field(default_factory=list)
    min_value: int = 0
    max_value: int = 0
    step: int = 1
    # Id of a BOOLEAN option in the same feature. While that option is true
    # this one is inert: the runtime ignores its value and we grey it out.
    disabled_by: str = ""

    def validate(self, value: str) -> bool:
        """Mirror of the runtime's ``valid_option_value``.

        Checking here means an invalid value never reaches state.toml, where
        it would be silently coerced or - worse - reject the whole file.
        """
        if self.type == BOOLEAN:
            return value in ("true", "false")
        if self.type == CHOICE:
            return any(c.value == value for c in self.choices)
        parsed = parse_canonical_int(value)
        if parsed is None:
            return False
        if not (self.min_value <= parsed <= self.max_value):
            return False
        # Step alignment is measured from min, not from zero.
        return (parsed - self.min_value) % self.step == 0


@dataclass
class Resource:
    feature: str
    id: str
    label: str
    description: str = ""
    file_patterns: str = ""
    file_description: str = ""
    format: str = "file"          # "file" | "directory" | "folder"
    required: bool = False

    @property
    def wants_directory(self) -> bool:
        return self.format in ("directory", "folder")

    def qt_filter(self) -> str:
        """Translate the manifest's comma list into a Qt dialog filter."""
        patterns = " ".join(p.strip() for p in self.file_patterns.split(",")
                            if p.strip())
        if not patterns:
            return "All files (*)"
        label = self.file_description or "Supported files"
        return f"{label} ({patterns});;All files (*)"


@dataclass
class Feature:
    id: str
    name: str
    description: str = ""
    group: str = "General"
    default_enabled: bool = False
    options: list[Option] = field(default_factory=list)
    resources: list[Resource] = field(default_factory=list)
    # True for the synthetic feature the runtime invents for a v1 manifest
    # that declares none. Those are a migration path and reject
    # set_feature_enabled, so we must not render them as toggles.
    legacy: bool = False


@dataclass
class Package:
    id: str
    version: str
    name: str
    description: str = ""
    author: str = ""
    features: list[Feature] = field(default_factory=list)
    path: Path | None = None

    def feature(self, feature_id: str) -> Feature | None:
        return next((f for f in self.features if f.id == feature_id), None)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def parse_canonical_int(value: str) -> int | None:
    """Accept only what the runtime's ``parse_canonical_int64`` accepts.

    No leading ``+``, no leading zeros, no trailing junk - so a value we write
    always reads back as the same integer.
    """
    text = (value or "").strip()
    negative = text.startswith("-")
    digits = text[1:] if negative else text
    if not digits.isdigit():
        return None
    if len(digits) > 1 and digits[0] == "0":
        return None
    return -int(digits) if negative else int(digits)


def _as_str(raw: object, fallback: str = "") -> str:
    """Manifests declare boolean/choice defaults as TOML strings and integer
    defaults as TOML integers. Normalise both to our canonical string form."""
    if isinstance(raw, bool):
        return "true" if raw else "false"
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, str):
        return raw
    return fallback


def parse_manifest(path: Path) -> Package | None:
    """Parse one manifest. Returns None when it is unusable.

    The runtime silently skips an unparseable manifest, which means a typo in
    a third-party mod looks exactly like the mod not being installed. The
    caller surfaces the None so the page can say so.
    """
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    if not isinstance(raw, dict):
        return None

    version = raw.get("format_version")
    if not isinstance(version, int) or not 1 <= version <= MAX_FORMAT_VERSION:
        return None
    for required in ("id", "version", "name"):
        if not isinstance(raw.get(required), str) or not raw[required]:
            return None

    package = Package(
        id=raw["id"],
        version=raw["version"],
        name=raw["name"],
        description=_as_str(raw.get("description")),
        author=_as_str(raw.get("author")),
        path=path.parent,
    )

    features = raw.get("feature")
    if not isinstance(features, list) or not features:
        # A manifest with no [[feature]] is format 1. The runtime synthesises a
        # single "legacy" feature for it and then refuses to toggle it.
        package.features = [Feature(id="legacy", name=package.name,
                                    description=package.description,
                                    legacy=True)]
        return package

    for entry in features:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        package.features.append(Feature(
            id=entry["id"],
            name=_as_str(entry.get("name"), entry["id"]),
            description=_as_str(entry.get("description")),
            group=_as_str(entry.get("group"), "General") or "General",
            default_enabled=bool(entry.get("default_enabled", False)),
        ))

    for entry in raw.get("option", []) or []:
        option = _parse_option(entry)
        if option is None:
            continue
        target = package.feature(option.feature)
        if target is not None:
            target.options.append(option)

    for entry in raw.get("resource", []) or []:
        resource = _parse_resource(entry)
        if resource is None:
            continue
        target = package.feature(resource.feature)
        if target is not None:
            target.resources.append(resource)

    # A dangling or non-boolean disabled_by is rejected by the runtime's
    # parser, so drop the link rather than render a control we would grey out
    # against an option that does not exist.
    for feature in package.features:
        booleans = {o.id for o in feature.options if o.type == BOOLEAN}
        for option in feature.options:
            if option.disabled_by and option.disabled_by not in booleans:
                option.disabled_by = ""

    return package


def _parse_option(entry: object) -> Option | None:
    if not isinstance(entry, dict):
        return None
    for required in ("feature", "id", "label"):
        if not isinstance(entry.get(required), str) or not entry[required]:
            return None
    kind = _as_str(entry.get("type"), BOOLEAN)
    if kind not in (BOOLEAN, CHOICE, INTEGER):
        return None

    option = Option(
        feature=entry["feature"], id=entry["id"], label=entry["label"],
        description=_as_str(entry.get("description")),
        group=_as_str(entry.get("group"), "General") or "General",
        type=kind,
        disabled_by=_as_str(entry.get("disabled_by")),
    )

    if kind == BOOLEAN:
        option.default = _as_str(entry.get("default"), "false")
        if option.default not in ("true", "false"):
            option.default = "false"
        return option

    if kind == CHOICE:
        for choice in entry.get("choice", []) or []:
            if not isinstance(choice, dict):
                continue
            value, label = choice.get("value"), choice.get("label")
            if isinstance(value, str) and isinstance(label, str):
                option.choices.append(Choice(value, label))
        if not option.choices:
            return None
        option.default = _as_str(entry.get("default"),
                                 option.choices[0].value)
        if not any(c.value == option.default for c in option.choices):
            option.default = option.choices[0].value
        return option

    # integer
    low, high = entry.get("min"), entry.get("max")
    if not isinstance(low, int) or not isinstance(high, int) or low > high:
        return None
    step = entry.get("step", 1)
    if not isinstance(step, int) or step <= 0:
        step = 1
    option.min_value, option.max_value, option.step = low, high, step
    option.default = _as_str(entry.get("default"), str(low))
    if not option.validate(option.default):
        option.default = str(low)
    return option


def _parse_resource(entry: object) -> Resource | None:
    if not isinstance(entry, dict):
        return None
    for required in ("feature", "id", "label"):
        if not isinstance(entry.get(required), str) or not entry[required]:
            return None
    return Resource(
        feature=entry["feature"], id=entry["id"], label=entry["label"],
        description=_as_str(entry.get("description")),
        file_patterns=_as_str(entry.get("file_patterns")),
        file_description=_as_str(entry.get("file_description")),
        format=_as_str(entry.get("format"), "file") or "file",
        required=bool(entry.get("required", False)),
    )


def scan(layout) -> tuple[list[Package], list[str]]:
    """Every installed package, plus the paths we could not parse.

    Mirrors the runtime's scan: ``packages/<id>/<version>/manifest.toml``, and
    the directory names must match the manifest's own id and version. The
    runtime aborts its WHOLE scan on a mismatch, so we report those loudly
    rather than quietly dropping one package.
    """
    root = layout.mod_packages
    packages: list[Package] = []
    problems: list[str] = []
    if not root.is_dir():
        return packages, problems

    for package_dir in sorted(root.iterdir()):
        if not package_dir.is_dir():
            continue
        for version_dir in sorted(package_dir.iterdir()):
            manifest = version_dir / "manifest.toml"
            if not manifest.is_file():
                continue
            package = parse_manifest(manifest)
            if package is None:
                problems.append(f"{package_dir.name}/{version_dir.name}: "
                                "manifest could not be read")
                continue
            if (package.id != package_dir.name
                    or package.version != version_dir.name):
                problems.append(
                    f"{package_dir.name}/{version_dir.name}: manifest says "
                    f"{package.id}/{package.version} - the runtime rejects "
                    "every mod when a path disagrees with its manifest")
                continue
            packages.append(package)
    return packages, problems


# --------------------------------------------------------------------------
# state.toml
# --------------------------------------------------------------------------


@dataclass
class FeatureState:
    enabled: bool | None = None            # None -> fall back to the manifest
    values: dict[str, str] = field(default_factory=dict)
    resources: dict[str, str] = field(default_factory=dict)


@dataclass
class State:
    # package_id -> pinned version ("" means "highest installed wins")
    versions: dict[str, str] = field(default_factory=dict)
    # (package_id, feature_id) -> FeatureState
    features: dict[tuple[str, str], FeatureState] = field(default_factory=dict)

    def get(self, package_id: str, feature_id: str) -> FeatureState:
        return self.features.setdefault((package_id, feature_id),
                                        FeatureState())

    def is_enabled(self, package: Package, feature: Feature) -> bool:
        entry = self.features.get((package.id, feature.id))
        if entry is None or entry.enabled is None:
            return feature.default_enabled
        return entry.enabled

    def value_of(self, package: Package, feature: Feature,
                 option: Option) -> str:
        entry = self.features.get((package.id, feature.id))
        if entry is not None and option.id in entry.values:
            candidate = entry.values[option.id]
            if option.validate(candidate):
                return candidate
        return option.default

    def resource_of(self, package: Package, feature: Feature,
                    resource: Resource) -> str:
        entry = self.features.get((package.id, feature.id))
        if entry is None:
            return ""
        return entry.resources.get(resource.id, "")


def _scalar_to_str(raw: object) -> str | None:
    """The runtime's reader coerces scalars; a table or array is fatal."""
    if isinstance(raw, bool):
        return "true" if raw else "false"
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, str):
        return raw
    return None


def load_state(path: Path) -> State:
    """Read state.toml, tolerating everything the runtime tolerates.

    A malformed file makes the runtime boot completely unmodded, so an
    unreadable one here is treated as empty rather than raised: the page then
    shows manifest defaults, and the first save rewrites it cleanly.
    """
    state = State()
    if not path.is_file():
        return state
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return state
    if not isinstance(raw, dict):
        return state

    for entry in raw.get("package", []) or []:
        if not isinstance(entry, dict):
            continue
        package_id = entry.get("id")
        if isinstance(package_id, str) and package_id:
            version = entry.get("version")
            state.versions[package_id] = version if isinstance(version, str) else ""

    for entry in raw.get("feature", []) or []:
        if not isinstance(entry, dict):
            continue
        package_id, feature_id = entry.get("package_id"), entry.get("id")
        if not isinstance(package_id, str) or not isinstance(feature_id, str):
            continue
        item = state.get(package_id, feature_id)
        if isinstance(entry.get("enabled"), bool):
            item.enabled = entry["enabled"]
        for key, value in (entry.get("values") or {}).items():
            text = _scalar_to_str(value)
            if text is not None:
                item.values[key] = text
        for key, value in (entry.get("resources") or {}).items():
            text = _scalar_to_str(value)
            if text is not None:
                item.resources[key] = text
    return state


def quote(text: str) -> str:
    """A TOML basic string, escaped the way the runtime escapes one."""
    out = [""]
    for char in text:
        if char == "\\":
            out.append("\\\\")
        elif char == '"':
            out.append('\\"')
        elif char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif char == "\t":
            out.append("\\t")
        elif ord(char) < 0x20:
            out.append("\\u%04X" % ord(char))
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def render_state(state: State, packages: list[Package]) -> str:
    """Emit state.toml.

    Only packages that are actually installed are written: a stale entry for a
    removed package is harmless to the runtime but confusing to read back, and
    the runtime would drop it on the next launch anyway.

    ``enabled`` is written on every feature. The runtime reads it with a plain
    ``toml::find<bool>``, not ``find_or``, so an omitted flag throws and takes
    the entire mod system down with it.

    Keys inside the values and resources tables are QUOTED, which the runtime's
    own writer does not do. Option ids may legally contain a dot, and a bare
    ``a.b = "x"`` parses back as a nested table, which the reader then rejects
    as non-scalar - bricking the file. Quoting round-trips correctly and is
    read identically.
    """
    lines = [f"format_version = {STATE_FORMAT_VERSION}"]
    installed = {p.id: p for p in packages}

    for package in packages:
        lines.append("")
        lines.append("[[package]]")
        lines.append(f"id = {quote(package.id)}")
        pinned = state.versions.get(package.id, "")
        if pinned:
            lines.append(f"version = {quote(pinned)}")

    for (package_id, feature_id), item in sorted(state.features.items()):
        package = installed.get(package_id)
        if package is None:
            continue
        feature = package.feature(feature_id)
        if feature is None or feature.legacy:
            continue
        enabled = (feature.default_enabled if item.enabled is None
                   else item.enabled)
        lines.append("")
        lines.append("[[feature]]")
        lines.append(f"package_id = {quote(package_id)}")
        lines.append(f"id = {quote(feature_id)}")
        lines.append(f"enabled = {'true' if enabled else 'false'}")
        if item.values:
            lines.append("[feature.values]")
            for key, value in sorted(item.values.items()):
                lines.append(f"{quote(key)} = {quote(value)}")
        if item.resources:
            lines.append("[feature.resources]")
            for key, value in sorted(item.resources.items()):
                lines.append(f"{quote(key)} = {quote(value)}")

    return "\n".join(lines) + "\n"


def save_state(path: Path, state: State, packages: list[Package]) -> None:
    """Write state.toml atomically.

    The runtime writes through a .tmp and renames for the same reason: a torn
    write is not a slightly-wrong mod list, it is every mod silently off.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = render_state(state, packages)
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# Pre-launch checks the launcher can actually make
# --------------------------------------------------------------------------


def problems_for(packages: list[Package], state: State) -> list[str]:
    """Selection errors we can detect without the runtime's byte-level plan.

    The full resolver runs in the runtime and refuses to launch on a conflict;
    these are the cheap checks that need only the manifests, so the player
    hears about them on this page rather than as a failed launch.
    """
    found: list[str] = []
    claimed: dict[str, str] = {}
    for package in packages:
        for feature in package.features:
            if feature.legacy or not state.is_enabled(package, feature):
                continue
            where = f"{package.name} / {feature.name}"
            for resource in feature.resources:
                if not resource.required:
                    continue
                path = state.resource_of(package, feature, resource)
                if not path:
                    found.append(f"{where}: {resource.label} must be set")
                elif not Path(path).exists():
                    found.append(f"{where}: {resource.label} no longer exists")
            for option in feature.options:
                value = state.value_of(package, feature, option)
                if not option.validate(value):
                    found.append(f"{where}: {option.label} has an invalid "
                                 f"value ({value!r})")
            key = f"{package.id}/{feature.id}"
            claimed[key] = where
    return found


# --------------------------------------------------------------------------
# Installing a .psxmod
# --------------------------------------------------------------------------

# The runtime's installer applies these limits; we apply the same ones rather
# than inventing our own, so a package that installs here also installs there.
MAX_ARCHIVE_FILES = 4096
MAX_EXPANDED_BYTES = 256 * 1024 * 1024


class InstallError(Exception):
    """Refused a .psxmod, with a reason worth showing the player."""


def _safe_member_path(name: str) -> Path:
    """Reject anything that would write outside the destination.

    A zip entry may legally contain ``..`` or an absolute path; extracting one
    blindly is the classic zip-slip. The runtime rejects both, so we do too
    rather than silently sanitising and installing something different from
    what the author shipped.
    """
    if name.startswith("/") or name.startswith("\\"):
        raise InstallError(f"archive contains an absolute path: {name}")
    if len(name) > 1 and name[1] == ":":
        raise InstallError(f"archive contains a drive-qualified path: {name}")
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise InstallError(f"archive contains a parent-directory path: {name}")
    if not parts:
        raise InstallError("archive contains an empty path")
    return Path(*parts)


def install_archive(layout, archive: Path) -> Package:
    """Install a .psxmod into ``<mods>/packages/<id>/<version>/``.

    Validates before writing anything, stages into a temporary directory and
    only then publishes, so a rejected or half-read archive cannot leave a
    partial package behind for the runtime to trip over.
    """
    import shutil
    import zipfile

    try:
        handle = zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        raise InstallError(f"not a readable zip archive: {exc}") from exc

    with handle:
        entries = [i for i in handle.infolist() if not i.is_dir()]
        if not entries:
            raise InstallError("archive is empty")
        if len(entries) > MAX_ARCHIVE_FILES:
            raise InstallError(
                f"archive has {len(entries)} files; the limit is "
                f"{MAX_ARCHIVE_FILES}")

        total = 0
        for info in entries:
            if info.compress_type not in (zipfile.ZIP_STORED,
                                          zipfile.ZIP_DEFLATED):
                raise InstallError(
                    f"{info.filename}: only stored and deflated entries are "
                    "supported (an encrypted or exotic archive is refused)")
            if info.flag_bits & 0x1:
                raise InstallError("archive is encrypted")
            total += info.file_size
            if total > MAX_EXPANDED_BYTES:
                raise InstallError(
                    "archive expands to more than "
                    f"{MAX_EXPANDED_BYTES // (1024 * 1024)} MiB")
            _safe_member_path(info.filename)

        names = {i.filename.replace("\\", "/") for i in entries}
        if "manifest.toml" not in names:
            raise InstallError(
                "no manifest.toml at the archive root - this does not look "
                "like a .psxmod package")

        staging = Path(tempfile.mkdtemp(prefix="psxmod-",
                                        dir=str(layout.mods)))
        try:
            for info in entries:
                target = staging / _safe_member_path(info.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                with handle.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

            package = parse_manifest(staging / "manifest.toml")
            if package is None:
                raise InstallError("manifest.toml is not a valid manifest")

            destination = layout.mod_packages / package.id / package.version
            if destination.exists():
                raise InstallError(
                    f"{package.name} {package.version} is already installed")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staging), str(destination))
            package.path = destination
            return package
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def remove_package(layout, package: Package) -> None:
    """Delete one installed version, and the package dir if it was the last."""
    import shutil

    target = layout.mod_packages / package.id / package.version
    if not target.is_dir():
        return
    shutil.rmtree(target)
    parent = target.parent
    try:
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass
