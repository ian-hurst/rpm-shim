# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
RPM shim module for use in virtualenvs
"""

import importlib
import importlib.util
import json
import logging
import platform
import subprocess
import sys
from pathlib import Path
from typing import List

PROJECT_NAME = "rpm-shim"
MODULE_NAME = "rpm"

logger = logging.getLogger(PROJECT_NAME)


class ShimAlreadyInitializingError(Exception):
    pass


def get_system_sitepackages() -> List[str]:
    """
    Gets a list of sitepackages directories of system Python interpreter(s).

    Returns:
        List of paths.
    """

    def get_sitepackages(interpreter):
        command = [
            interpreter,
            "-c",
            "import json, site; print(json.dumps(site.getsitepackages()))",
        ]
        output = subprocess.check_output(command)
        return json.loads(output.decode())

    majorver, minorver, _ = platform.python_version_tuple()
    # try platform-python first (it could be the only interpreter present on the system)
    interpreters = [
        "/usr/libexec/platform-python",
        f"/usr/bin/python{majorver}",
        f"/usr/bin/python{majorver}.{minorver}",
    ]
    result = []
    for interpreter in interpreters:
        if not Path(interpreter).is_file():
            continue
        sitepackages = get_sitepackages(interpreter)
        formatted_list = "\n".join(sitepackages)
        logger.debug(f"Collected sitepackages for {interpreter}:\n{formatted_list}")
        result.extend(sitepackages)
    return list(set(result))


def try_path(path: str) -> bool:
    """
    Tries to load system RPM module from the specified path.

    Returns:
        True if successful, False otherwise.
    """
    module_path = Path(path) / MODULE_NAME
    if not module_path.is_dir():
        return False
    sys.path.insert(0, path)
    try:
        import_helper(module_path)
        # sanity check
        confdir = sys.modules[__name__].expandMacro("%getconfdir")
        return Path(confdir).is_dir()
    finally:
        del sys.path[0]
    return False


def import_helper(path: Path) -> None:
    """
    Helps import rpm in cases where the shared object file extensions include the python
    interpreter version

    Args:
        path (Path): the absolute path to the rpm module

    Returns:
        None
    """
    try:
        importlib.reload(sys.modules[__name__])
    except ModuleNotFoundError as e:
        logger.debug(
            f"The {e.name} module was not found in {path}, try loading binary extensions directly"
        )
        # get a list of binary extensions in path whose suffix includes the python version; the
        # import failure may have been from a mismatch in expected suffixes when a different python
        # interpreter is in use (see importlib.machinery.EXTENSION_SUFFIXES)
        extensions = list(path.glob("*.cpython*.so"))
        # if none were found, this wasn't the reason for the failure, and we can't help
        if len(extensions) == 0:
            raise
        logger.debug(f"Found binary extensions:\n{extensions}")
        for extension in extensions:
            # get the non-suffix part of '_rpm.cpython-39-x86_64-linux-gnu.so', i.e. '_rpm'
            prefix = extension.name.split(".")[0]
            load_module_by_path(f"{__name__}.{prefix}", extension)
        # reload again, otherwise sys.modules[__name__] is still rpm-shim
        logger.debug(f"imported extensions, reloading {__name__}")
        importlib.reload(sys.modules[__name__])


def load_module_by_path(module_name: str, path: Path) -> None:
    """
    Imports a python module by file path

    Args:
        module_name (str): name of the module to import
        path (Path): absolute path to the module to import

    Returns:
        None
    """
    if module_name in sys.modules:
        logger.debug(f"{module_name} already loaded")
        return
    logger.debug(f"importing {path} as {module_name}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None:
        return
    if spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules[module_name] = module


def initialize() -> None:
    """
    Initializes the shim. Tries to load system RPM module and replace itself with it.
    """
    for path in get_system_sitepackages():
        logger.debug(f"Trying {path}")
        try:
            if try_path(path):
                logger.debug("Import successfull")
                return
        except ShimAlreadyInitializingError:
            continue
        except Exception as e:
            logger.debug(f"Exception: {type(e)}: {e}")
            continue
    else:
        raise ImportError(
            "Failed to import system RPM module. "
            "Make sure RPM Python bindings are installed on your system."
        )


# avoid repeated initialization of the shim module
try:
    _shim_module_initializing_
except NameError:
    _shim_module_initializing_: bool = True
    initialize()
else:
    raise ShimAlreadyInitializingError
