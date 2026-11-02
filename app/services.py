"""Lazily-created singletons, wired in as FastAPI dependencies so tests can override them."""
from __future__ import annotations

from functools import lru_cache

from .graph import Graph
from .sarvam import SarvamClient


@lru_cache(maxsize=1)
def get_graph() -> Graph:
    return Graph()


@lru_cache(maxsize=1)
def get_sarvam() -> SarvamClient:
    return SarvamClient()
