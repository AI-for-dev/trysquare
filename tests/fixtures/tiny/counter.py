"""The code a measured agent would be asked to change.

Small enough to read at a glance and real enough that the declared test command is a
real command. No dependency to install, which is what keeps a validation replayable from
a tag and a diff months later.
"""


def total(items):
    return sum(items)
