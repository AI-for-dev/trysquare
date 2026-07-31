"""The suite is not a terminal, but `python -m unittest` inherits one.

Not every test captures stdout - `TestReplayRescore` calls `main()` straight - so
without this a live progress bar would be drawn into the middle of a test run, and
an assertion would eventually be written against an escape sequence. A test that
wants the bar builds `progress.Bar` directly, which does not consult this.
"""

import os

from trysquare.progress import OFF

os.environ.setdefault(OFF, "1")
