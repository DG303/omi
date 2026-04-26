"""Authentication primitives for omi-cli.

Two flows are wired through this package:

* :mod:`omi_cli.auth.api_key` — paste-in dev API key (``omi_dev_*``). Primary
  flow, suitable for agents, CI, and any headless context.
* :mod:`omi_cli.auth.oauth` — Firebase OAuth browser flow. Stubbed in v0.1.0
  pending backend work to support a localhost callback redirect; the stub
  surfaces a clear error and points users at the API-key flow.

Common token persistence lives in :mod:`omi_cli.auth.store`.
"""

from omi_cli.auth.api_key import login_with_api_key, validate_api_key_format
from omi_cli.auth.store import clear_credentials

__all__ = [
    "login_with_api_key",
    "validate_api_key_format",
    "clear_credentials",
]
