"""Reload edited project modules without restarting Streamlit.

Streamlit keeps its Python process (and imported modules) alive between reruns, and its
own file watcher does not always notice edits to modules the app imports. The result is
the app running old code ("object has no attribute ...") until it is restarted.
reload_if_changed() compares each module file's modification time with the last time it
looked and reloads anything that changed.
"""

import importlib
import logging
import sys
from pathlib import Path

logger = logging.getLogger("pawpal.devreload")
_seen: dict[str, float] = {}


def reload_if_changed(names: list[str]) -> list[str]:
    """Reload the already-imported modules in `names` whose source file changed.

    Pass modules in dependency order (a module before the ones that import it). Once one
    module has been reloaded, every later one is reloaded too, so they pick up its new
    classes. The first call for a module only records its timestamp. Returns the names
    that were reloaded.
    """
    reloaded: list[str] = []
    for name in names:
        module = sys.modules.get(name)
        file = getattr(module, "__file__", None)
        if not file:
            continue
        mtime = Path(file).stat().st_mtime
        previous = _seen.get(name)
        _seen[name] = mtime
        if reloaded or (previous is not None and mtime != previous):
            importlib.reload(module)
            reloaded.append(name)
            logger.info("Reloaded edited module %s", name)
    return reloaded
