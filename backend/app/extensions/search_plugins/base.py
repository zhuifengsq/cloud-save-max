from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseSearchPlugin(ABC):
    """Implement this class in a ``<key>_plugin.py`` file.

    ``search`` should return dictionaries containing a title and a share URL.
    Accepted names are ``taskname``/``title`` and ``shareurl``/``url``.
    """

    key: str = ""
    name: str = ""
    description: str = ""
    timeout_seconds: float = 8.0

    @abstractmethod
    def search(self, keyword: str) -> list[dict[str, Any]]:
        raise NotImplementedError
