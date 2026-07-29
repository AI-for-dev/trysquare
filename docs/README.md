# Building the documentation

```bash
uv venv && uv pip install -e ".[docs]"
.venv/bin/python -m sphinx -b html docs docs/_build/html -W
# or
make -C docs html
```

Written in **MyST Markdown**, not reStructuredText: the rest of this project's prose is
Markdown, and one syntax is easier to keep good than two.

`-W` turns warnings into errors, deliberately. A warning is a defect in the
documentation, and the same standard applies here as to the code.

## Layout

```
docs/
  index.md              landing page, and the one-page summary
  guide/                task-oriented: how to do a thing
    getting-started.md  install, plan, measure, read the output
    concepts.md         the seven words everything else assumes
    writing-a-scenario.md
    validators.md       the three modes and their shared contract
    invariants.md       the eight rules, and the defect behind each
    parity.md           proving this tool matches the one it replaces
    troubleshooting.md  every refusal, and its reasoning
  reference/            lookup-oriented: what a key or a flag does
    cli.md
    scenario-schema.md
    config-schema.md
    outputs.md
    api.md              generated from docstrings
```

The API page is generated from docstrings, which in this project carry the *reasoning*
behind each rule rather than restating the signature. `autodoc_member_order = "bysource"`
keeps them in source order, because that order is part of the argument.

## Documentation dependencies are not tool dependencies

Sphinx and MyST live in the `docs` optional group and are never imported by `etabli/`.
The tool itself keeps **zero runtime dependencies**, which is what makes it usable
without an install step.
