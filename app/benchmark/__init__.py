"""Benchmark-only entry points, mounted opt-in, never on by default.

Nothing under this package is part of the served API surface: each
module here exists to compare fastgeoapi/pygeoapi against another tile
or feature server under identical load, in the same process.
"""
