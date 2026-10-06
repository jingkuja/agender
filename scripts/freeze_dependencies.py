"""Freeze the installed dependency closure; run after upgrading and testing a clean venv."""
import tomllib
from importlib.metadata import distribution
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

root = Path(__file__).resolve().parents[1]
project = tomllib.loads((root / 'pyproject.toml').read_text())['project']


def closure(requirements):
    found = {}
    processed = {}
    pending = [(value, {''}) for value in requirements]
    while pending:
        value, parent_extras = pending.pop()
        req = Requirement(value)
        # All pinned packages have portable wheels; Windows-only extras are
        # intentionally excluded from this macOS/Linux deployment lock.
        if req.marker and not any(req.marker.evaluate({'extra': extra}) for extra in parent_extras):
            continue
        name = canonicalize_name(req.name)
        extras = {''} | set(req.extras)
        if name in processed and extras <= processed[name]:
            continue
        processed[name] = processed.get(name, set()) | extras
        dist = distribution(req.name)
        found[name] = dist.version
        pending.extend((value, processed[name]) for value in dist.requires or [])
    return found


runtime = closure(project['dependencies'])
all_deps = closure(project['dependencies'] + project['optional-dependencies']['dev'])
for file, entries, prefix in (
    ('requirements.lock', runtime, ''),
    ('requirements-dev.lock', {k: v for k, v in all_deps.items() if k not in runtime}, '-r requirements.lock\n'),
):
    (root / file).write_text('# Pinned installed versions; macOS/Linux, Python 3.12+. Regenerate with scripts/freeze_dependencies.py.\n'
                            + prefix + ''.join(f'{k}=={v}\n' for k, v in sorted(entries.items())))
