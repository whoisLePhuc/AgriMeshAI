"""Compatibility shim for Python 3.10 vs 3.11+"""
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib
    except ImportError:
        raise ImportError("Python 3.10 requires: pip install tomli")
