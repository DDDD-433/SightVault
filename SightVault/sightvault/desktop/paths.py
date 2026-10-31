"""Per-user storage folders and bundled resource locations for the desktop app."""
from dataclasses import dataclass
import os
from pathlib import Path
import sys
import tempfile

from . import APP_NAME


class UnsafePathError(ValueError):
    """A configured storage folder is unusable or would write into an unsafe place."""


def is_frozen():
    return bool(getattr(sys, 'frozen', False))


def resource_root():
    """Folder holding sightvault/, handoff/ and samples/. Read-only inside a bundle."""
    bundle = getattr(sys, '_MEIPASS', None)
    if is_frozen() and bundle:
        # PyInstaller macOS bundles put real data in Resources and symlink it from
        # Frameworks. Use the actual data root so manifest confinement stays strict.
        resources = Path(bundle).parent / 'Resources'
        if Path(bundle).name == 'Frameworks' and (resources / 'handoff' / 'MODEL_MANIFEST.json').is_file():
            return resources.resolve()
        return Path(bundle).resolve()
    return Path(__file__).resolve().parents[2]


def bundle_root():
    """The installed application folder (the .app on macOS, the install folder on Windows)."""
    if not is_frozen():
        return None
    executable = Path(sys.executable).resolve()
    for parent in executable.parents:
        if parent.suffix == '.app':
            return parent
    return executable.parent


@dataclass(frozen=True)
class DesktopPaths:
    base: Path
    data: Path
    models: Path
    logs: Path
    resources: Path
    models_writable: bool = True

    @property
    def lock_file(self):
        return self.base / 'instance.lock'

    @property
    def instance_file(self):
        return self.base / 'instance.json'

    @property
    def log_file(self):
        return self.logs / 'sightvault.log'

    @property
    def setup_log_dir(self):
        return self.logs / 'setup'

    def as_dict(self):
        return {'base': str(self.base), 'data': str(self.data), 'models': str(self.models),
                'logs': str(self.logs), 'resources': str(self.resources),
                'models_writable': self.models_writable}


def default_locations(system=None, env=None, home=None):
    """Return the platform's per-user (base, logs) folders without creating them."""
    system = system or sys.platform
    env = os.environ if env is None else env
    home = Path(home) if home is not None else Path.home()
    if system == 'darwin':
        return (home / 'Library' / 'Application Support' / APP_NAME,
                home / 'Library' / 'Logs' / APP_NAME)
    if system.startswith('win'):
        base = Path(env.get('LOCALAPPDATA') or home / 'AppData' / 'Local') / APP_NAME
        return base, base / 'logs'
    data_home = Path(env.get('XDG_DATA_HOME') or home / '.local' / 'share')
    state_home = Path(env.get('XDG_STATE_HOME') or home / '.local' / 'state')
    return data_home / APP_NAME, state_home / APP_NAME / 'logs'


def checked_folder(raw, label, *, home, forbid_inside=None):
    """Validate one storage folder path. Nothing is created here."""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise UnsafePathError(f'{label} folder must be an absolute path: {raw}')
    # realpath resolves symlinks in the existing part of the path, even if the tail is new.
    resolved = Path(os.path.realpath(path))
    if resolved == Path(resolved.anchor):
        raise UnsafePathError(f'{label} folder cannot be a filesystem root: {resolved}')
    if resolved == Path(os.path.realpath(Path(home).expanduser())):
        raise UnsafePathError(f'{label} folder cannot be the home folder itself: {resolved}')
    if forbid_inside is not None:
        bundle = Path(os.path.realpath(forbid_inside))
        if resolved == bundle or resolved.is_relative_to(bundle):
            raise UnsafePathError(f'{label} folder cannot be inside the read-only application bundle: {resolved}')
    if resolved.exists() and not resolved.is_dir():
        raise UnsafePathError(f'{label} path exists but is not a folder: {resolved}')
    return resolved


def resolve_paths(env=None, system=None, home=None, resources=None, frozen=None, bundle=None):
    """Choose per-user data, model and log folders.

    SIGHTVAULT_HOME replaces the platform base folder. SIGHTVAULT_DATA and
    SIGHTVAULT_MODELS override individual folders, for example to reuse weights
    already downloaded for a source checkout. In an installed build no folder
    may sit inside the application bundle, which is read-only and replaced on update.
    """
    env = os.environ if env is None else env
    home = Path(home) if home is not None else Path.home()
    resources = Path(resources) if resources is not None else resource_root()
    frozen = is_frozen() if frozen is None else frozen
    forbid = (bundle or bundle_root() or resources) if frozen else None
    base_default, logs_default = default_locations(system, env, home)
    if env.get('SIGHTVAULT_HOME'):
        base = checked_folder(env['SIGHTVAULT_HOME'], 'SIGHTVAULT_HOME', home=home, forbid_inside=forbid)
        logs = base / 'logs'
    else:
        base = checked_folder(base_default, 'Application data', home=home, forbid_inside=forbid)
        logs = checked_folder(logs_default, 'Log', home=home, forbid_inside=forbid)
    data = checked_folder(env.get('SIGHTVAULT_DATA') or base / 'data', 'Data', home=home, forbid_inside=forbid)
    models = checked_folder(env.get('SIGHTVAULT_MODELS') or base / 'models', 'Model', home=home, forbid_inside=forbid)
    return DesktopPaths(base=base, data=data, models=models, logs=logs, resources=resources)


def writable(folder):
    try:
        with tempfile.NamedTemporaryFile(dir=folder, prefix='.write-check-', delete=True):
            pass
        return True
    except OSError:
        return False


def prepare(paths):
    """Create the folders and confirm the ones the app writes to are writable."""
    for folder in (paths.base, paths.data, paths.logs, paths.setup_log_dir):
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not writable(folder):
            raise UnsafePathError(f'SightVault cannot write to {folder}. Check its permissions or choose another '
                                  'location with SIGHTVAULT_HOME.')
    models_writable = True
    try:
        paths.models.mkdir(parents=True, exist_ok=True, mode=0o700)
        models_writable = writable(paths.models)
    except OSError:
        models_writable = False
    # A read-only model folder still works for inference; only downloads need write access.
    return DesktopPaths(base=paths.base, data=paths.data, models=paths.models, logs=paths.logs,
                        resources=paths.resources, models_writable=models_writable)
