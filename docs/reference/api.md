# Python API

The package is importable, and the split below is the architecture: **the pure half
never touches the world**, which is why the methodological invariants have tests that
need no network, no clone, and no API key.

```{contents}
:local:
:depth: 1
```

## The pure core

### `trysquare.scenario`

```{eval-rst}
.. automodule:: trysquare.scenario
   :members:
   :undoc-members:
   :show-inheritance:
```

### `trysquare.measure`

```{eval-rst}
.. automodule:: trysquare.measure
   :members:
   :undoc-members:
```

### `trysquare.verdict`

```{eval-rst}
.. automodule:: trysquare.verdict
   :members:
   :undoc-members:
```

### `trysquare.table`

```{eval-rst}
.. automodule:: trysquare.table
   :members:
   :undoc-members:
```

### `trysquare.parity`

```{eval-rst}
.. automodule:: trysquare.parity
   :members:
   :undoc-members:
```

## The effectful shell

### `trysquare.config`

```{eval-rst}
.. automodule:: trysquare.config
   :members:
   :undoc-members:
```

### `trysquare.repo`

```{eval-rst}
.. automodule:: trysquare.repo
   :members:
   :undoc-members:
```

### `trysquare.agent`

```{eval-rst}
.. automodule:: trysquare.agent
   :members:
   :undoc-members:
```

### `trysquare.validation`

```{eval-rst}
.. automodule:: trysquare.validation
   :members:
   :undoc-members:
```

### `trysquare.outputs`

```{eval-rst}
.. automodule:: trysquare.outputs
   :members:
   :undoc-members:
```

### `trysquare.runner`

```{eval-rst}
.. automodule:: trysquare.runner
   :members:
   :undoc-members:
```

### `trysquare.cli`

```{eval-rst}
.. automodule:: trysquare.cli
   :members: main, build_parser
```
