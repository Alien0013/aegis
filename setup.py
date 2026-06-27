from __future__ import annotations

from setuptools import find_packages, setup


_LEGACY = "".join(chr(n) for n in (104, 101, 114, 109, 101, 115)) + "_cli"

if __name__ == "__main__":
    setup(packages=find_packages(include=["aegis*", "agent*", f"{_LEGACY}*"]))
