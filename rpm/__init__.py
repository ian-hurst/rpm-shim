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

    def get_suffixes(interpreter):
        print_suffixes = [
            'import json, importlib.machinery',
            'print(json.dumps(importlib.machinery.EXTENSION_SUFFIXES))',
        ]
        command = [
            interpreter,
            '-c',
            ';'.join(print_suffixes)
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
        suffixes = get_suffixes(interpreter)
        formatted_list = "\n".join(sitepackages + suffixes)
        logger.debug(f"Collected sitepackages, suffixes for {interpreter}:\n{formatted_list}")
        result.append([sitepackages, suffixes])
    return result


def try_path(path: str, suffixes: List) -> bool:
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
        import_helper(module_path, suffixes)
        # sanity check
        confdir = sys.modules[__name__].expandMacro("%getconfdir")
        return Path(confdir).is_dir()
    finally:
        del sys.path[0]
    return False


def import_helper(path: Path, suffixes: List) -> None:
    """
    Helps import rpm in cases where the shared object file extensions include the python
    interpreter version

    Args:
        path (Path): the absolute path to the rpm module
        suffixes (List): a list of extension suffixes valid for the python interpreter

    Returns:
        None
    """
    try:
        importlib.reload(sys.modules[__name__])
    except ModuleNotFoundError as e:
        logger.debug(f"Failed to import {e.name} from {path}, looking for extensions with valid suffixes")
        stem = path / e.name.split('.')[1]
        for suffix in suffixes:
            so = Path(str(stem) + suffix)
            logger.debug(f"Looking for {so}")
            if not so.exists():
                continue
            load_module_by_path(e.name, so)
            importlib.reload(sys.modules[__name__])
            return
        else:
            logger.debug(f"Give up on {path}")


def load_module_by_path(module_name: str, path: Path) -> None:
    """
    Imports a python module by file path

    Args:
        module_name (str): name of the module to import
        path (Path): absolute path to the module to import

    Returns:
        None
    """
    logger.debug(f"importing {path} as {module_name}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


def initialize() -> None:
    """
    Initializes the shim. Tries to load system RPM module and replace itself with it.
    """
    for site_packages, suffixes in get_system_sitepackages():
        for site in site_packages:
            logger.debug(f"Trying {site}")
            try:
                if try_path(site, suffixes):
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
