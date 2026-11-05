"""The Anthropic API key the app uses, and where it came from.

A key can come from three places, in this order of preference:

1. **Entered in the app** on the setup page, and kept only in memory for
   as long as the server runs.
2. **Entered in the app and remembered**: saved to ``api-key`` in the data
   folder, so the next start doesn't ask again. The file holds the key in
   plain text, like a ``.env`` file; on macOS and Linux only its owner can
   read it.
3. **The environment** (``ANTHROPIC_API_KEY``, from the shell or ``.env``).

A key entered in the app replaces the environment's for that run. Offline
mode needs no key at all. The key never leaves this module except to build
the API client: the browser only ever sees its last four characters.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

KEY_FILE = "api-key"
MAX_KEY_LENGTH = 400

Source = Literal["entered", "saved", "environment", "offline"]


class CredentialsError(Exception):
    """A key couldn't be saved or removed."""


@dataclass(frozen=True)
class KeyStatus:
    """What the browser may know about the key: never the key itself."""

    configured: bool
    source: Source | None
    hint: str | None  # the last four characters, e.g. "…a1B2"


class ApiKeys:
    """The key in use. Safe to share between threads.

    ``apply`` is called with each new key (or None once it's forgotten) so
    the API client can be rebuilt with it.
    """

    def __init__(
        self,
        *,
        path: Path | None,
        environ_key: str | None = None,
        apply: Callable[[str | None], None] = lambda key: None,
        offline: bool = False,
    ) -> None:
        self._path = path
        self._apply = apply
        self._offline = offline
        self._lock = threading.Lock()
        self._key: str | None = None
        self._source: Source | None = None
        self._environ_key = (environ_key or "").strip() or None

        saved = self._read()
        if saved:
            self._key, self._source = saved, "saved"
        elif self._environ_key:
            self._key, self._source = self._environ_key, "environment"
        if self._key:
            apply(self._key)

    @property
    def configured(self) -> bool:
        return self._offline or self._key is not None

    def status(self) -> KeyStatus:
        if self._offline:
            return KeyStatus(True, "offline", None)
        with self._lock:
            key = self._key
            return KeyStatus(key is not None, self._source, f"…{key[-4:]}" if key else None)

    def use(self, key: str, *, remember: bool) -> None:
        """Use ``key`` from now on, saving it only if ``remember`` is set.

        Not remembering also removes a key saved earlier: the player has
        asked for it not to be kept.
        """
        key = key.strip()
        with self._lock:
            if remember:
                self._write(key)
            else:
                self._delete()
            self._key = key
            self._source = "saved" if remember else "entered"
            self._apply(key)

    def forget(self) -> None:
        """Stop using the key entered in the app, and delete the saved copy.

        A key from the environment can't be forgotten from here, so the app
        falls back to it if there is one.
        """
        with self._lock:
            self._delete()
            self._key = self._environ_key
            self._source = "environment" if self._environ_key else None
            self._apply(self._key)

    # --- the file ---------------------------------------------------------------

    def _read(self) -> str | None:
        if self._path is None or not self._path.is_file():
            return None
        try:
            return self._path.read_text(encoding="utf-8").strip() or None
        except OSError as exc:
            log.warning("couldn't read the saved API key at %s: %s", self._path, exc)
            return None

    def _write(self, key: str) -> None:
        if self._path is None:
            raise CredentialsError("there is no data folder to save the key in")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(dir=self._path.parent, prefix=".", suffix=".tmp")
            try:
                if sys.platform != "win32":
                    os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(key + "\n")
                os.replace(temp, self._path)
            except BaseException:
                Path(temp).unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise CredentialsError(f"couldn't save the key to {self._path}: {exc}") from exc

    def _delete(self) -> None:
        if self._path is None:
            return
        try:
            self._path.unlink(missing_ok=True)
        except OSError as exc:
            raise CredentialsError(f"couldn't delete the saved key at {self._path}: {exc}") from exc
