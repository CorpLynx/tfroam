"""The prebuilt Rover frontend, shipped as package data (no Node needed at runtime)."""

from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable


def dist() -> Traversable:
    """Root of the bundled Vue build (``index.html``, ``js/``, ``css/``, ``img/``)."""
    return files("rover.ui").joinpath("dist")
