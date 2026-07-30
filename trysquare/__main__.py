"""`python -m trysquare`, which is the installed `trysquare` command exactly.

Both go through `scripts.cli_trysquare`, so a clone and an install answer an
interrupt the same way. Anything else would be a difference between how a
measurement is launched and how it is documented.
"""

import sys

from .scripts.cli_trysquare import main

sys.exit(main())
