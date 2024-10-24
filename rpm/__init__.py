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
import textwrap
from pathlib import Path
from typing import Any, List

PROJECT_NAME = "rpm-shim"
MODULE_NAME = "rpm"

logger = logging.getLogger(PROJECT_NAME)


class ShimAlreadyInitializingError(Exception):
    pass


def get_system_sitepackages() -> List[List[Any]]:
    """
    Gets a list of sitepackages directories of system Python interpreter(s).

    Returns:
        List of paths.
    """

    def get_sitepackages_and_suffixes(interpreter):
        script = textwrap.dedent(
            """
            import importlib
            import importlib.machinery
            import json
            import site
            print(
                json.dumps(
                    {
                        "sitepackages": site.getsitepackages(),
                        "suffixes": importlib.machinery.EXTENSION_SUFFIXES,
                    }
                )
            )
            """
        )
        output = subprocess.check_output([interpreter], input=script.encode())
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
        sitepackages_suffixes = get_sitepackages_and_suffixes(interpreter)
        logger.debug(
            f"Collected sitepackages, suffixes for {interpreter}:\n{sitepackages_suffixes}"
        )
        result.append(sitepackages_suffixes)
    return result


def try_path(path: str, suffixes: List[str]) -> bool:
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
    attempted_modules = []
    while True:
        try:
            importlib.reload(sys.modules[__name__])
        except ModuleNotFoundError as e:
            if e.name in attempted_modules:
                logger.debug(f"Already tried {e.name} in {path}, giving up")
                raise
            attempted_modules.append(e.name)
            logger.debug(
                f"Module {e.name} not found in {path}, "
                "looking for any binary extensions with valid suffixes"
            )
            try_import_binary_modules(path, e.name, suffixes)
        else:
            logger.debug(f"Reloaded {__name__}")
            return


def try_import_binary_modules(path: Path, module: str, suffixes: List) -> bool:
    """
    Finds and imports binary modules in {path} matching {name} and {suffixes}

    Args:
        path (Path): the path to a module, i.e. /usr/lib64/python3.9/site-packages/rpm/
        module (str): the name of a module to import, i.e. 'rpm._rpm'
        suffixes (List[str]): a list of extension suffixes to check for (see
                              importlib.machinery.EXTENSION_SUFFIXES)

    Returns:
        True if the module was loaded, False otherwise
    """
    # get the child module name, i.e. just '_rpm' from 'rpm._rpm'
    child = module.rpartition(".")[-1]
    for suffix in suffixes:
        # a file named {child}{suffix} may exist in {path}, i.e.
        #
        # /usr/lib64/python3.9/site-packages/rpm/_rpm.cpython-39-x86_64-linux-gnu.so
        #
        # if so we'll try loading it as rpm._rpm
        so = path / f"{child}{suffix}"
        if not so.is_file():
            logger.debug(f"{so} exists but isn't a file, ignoring")
            continue
        if load_module_by_path(module, so):
            return True
    else:
        logger.debug(f"No suffixes matched {child} found in {path}, giving up")
        return False


def load_module_by_path(module_name: str, path: Path) -> bool:
    """
    Imports a python module by file path

    Args:
        module_name (str): name of the module to import
        path (Path): absolute path to the module to import
    """
    logger.debug(f"Try loading {module_name} from {path}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None:
        logger.debug(f"No spec for {module_name} in {path}")
        return False
    if spec.loader is None:
        logger.debug(f"No loader in spec for {module_name}")
        return False
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules[module_name] = module
    logger.debug(f"Loaded {module_name} from {path}")
    return True


def initialize() -> None:
    """
    Initializes the shim. Tries to load system RPM module and replace itself with it.
    """
    for i in get_system_sitepackages():
        sitepackages = i["sitepackages"]
        suffixes = i["suffixes"]
        for site in sitepackages:
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
