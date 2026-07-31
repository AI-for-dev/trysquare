"""What `trysquare init` writes: the skeleton of *your* experiment.

No scenario ships with the tool - an experiment is about your repository and
your question, and a shipped one would be somebody else's. What can ship is the
shape: every mandatory field present, carrying a placeholder, and one file - the
validator - deliberately left for you to write. `validate` therefore refuses a
fresh skeleton by name, and the refusal is the feature: nothing runnable exists
until the experiment is yours.

Pure: strings out. Writing them is `cli.cmd_init`'s job.
"""

SCENARIO = """\
# The skeleton of an experiment. Anything reading like a placeholder is one, and
# the harness never guesses: provider, model, thinking, etalon and repetitions
# are mandatory here and inherited from nowhere.

[scenario]
name = "my-experiment"
title = "What this experiment decides, in one sentence"
hypothesis = "hypothesis.md"    # declared before measuring, on purpose

[task]
repo = "my-repo"                # a logical name; trysquare.toml says where it is
etalon = "etalon-v1"            # a tag of that repository, never the working tree
prompt = "prompt.md"            # inline text works too

[agent]
provider = "your-provider"
model = "your-model"
thinking = "off"

[protocol]
repetitions = 10                # declared in advance, never raised "to see"
concurrency = 5
timeout = 900

# A grid: declaration order fixes the table's order. The first value of an axis
# is the baseline and declares no delta; every other value must, so a typo is
# refused instead of silently duplicating the baseline under two names.
[axes]
thinking = ["off", "high"]

[values.thinking.high]
thinking = "high"

[[validation]]
mode = "script"
command = "score.py"            # yours to write - `init` deliberately does not.
                                # `examples/validator.py` in the trysquare
                                # repository is a whole one, built on trysquare.assay.
metrics = ["in_scope", "delivered"]   # a contract: a validator omitting one
                                      # makes the run invalid, not false

[verdict]
criterion = "in_scope"
reference = { thinking = "off" }
validity = ["delivered"]
"""

PROMPT = """\
Replace this file with the task itself, exactly as the agent should receive it.
It is sent verbatim: the harness never rewrites, prefixes or completes it.
"""

HYPOTHESIS = """\
# Hypothesis

Written before measuring, on purpose: a hypothesis recorded after the numbers
exist is a caption, not a prediction. One or two sentences saying which gap you
expect, in which direction, and what would count as nothing.
"""

CONFIG = """\
# This machine's paths. A config file may only supply machine paths and load
# fallbacks - what is measured lives in the scenario, mandatorily, so the same
# experiment file measures the same thing on every machine.

[repos]
my-repo = "/path/to/your/checkout"    # or a git URL, cloned once at the etalon tag

[defaults]
workdir = "$TMPDIR/trysquare"
"""
