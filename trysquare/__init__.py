# SPDX-License-Identifier: BSD-3-Clause
"""trysquare - a scenario harness for measuring coding agents reproducibly."""

from importlib.metadata import PackageNotFoundError, version

# Read from the installed distribution rather than written here. The version was declared
# in three places - this file, `pyproject.toml` and `docs/conf.py` - with nothing deriving
# it, so a release could ship a wheel and a documentation page that disagreed about what
# it was. `pyproject.toml` is the one that names it.
try:
    __version__ = version("trysquare")
except PackageNotFoundError:  # a source tree nobody installed
    __version__ = "0+unknown"
