# Python API

The package is importable, and the split below is the architecture: **the pure half
never touches the world**, which is why the methodological invariants have tests that
need no network, no clone, and no API key.

```{contents}
:local:
:depth: 1
```

## The pure core

### `etabli.scenario`

```{eval-rst}
.. automodule:: etabli.scenario
   :members:
   :undoc-members:
   :show-inheritance:
```

### `etabli.measure`

```{eval-rst}
.. automodule:: etabli.measure
   :members:
   :undoc-members:
```

### `etabli.verdict`

```{eval-rst}
.. automodule:: etabli.verdict
   :members:
   :undoc-members:
```

### `etabli.table`

```{eval-rst}
.. automodule:: etabli.table
   :members:
   :undoc-members:
```

### `etabli.parity`

```{eval-rst}
.. automodule:: etabli.parity
   :members:
   :undoc-members:
```

## The effectful shell

### `etabli.config`

```{eval-rst}
.. automodule:: etabli.config
   :members:
   :undoc-members:
```

### `etabli.repo`

```{eval-rst}
.. automodule:: etabli.repo
   :members:
   :undoc-members:
```

### `etabli.agent`

```{eval-rst}
.. automodule:: etabli.agent
   :members:
   :undoc-members:
```

### `etabli.validation`

```{eval-rst}
.. automodule:: etabli.validation
   :members:
   :undoc-members:
```

### `etabli.outputs`

```{eval-rst}
.. automodule:: etabli.outputs
   :members:
   :undoc-members:
```

### `etabli.runner`

```{eval-rst}
.. automodule:: etabli.runner
   :members:
   :undoc-members:
```

### `etabli.cli`

```{eval-rst}
.. automodule:: etabli.cli
   :members: main, build_parser
```
