# Changelog

## [0.4.0](https://github.com/AI-for-dev/trysquare/compare/v0.3.0...v0.4.0) (2026-10-04)


### Features

* keep the provider's key out of the sandbox ([#96](https://github.com/AI-for-dev/trysquare/issues/96)) ([8883485](https://github.com/AI-for-dev/trysquare/commit/8883485e587a3ecef021e3455e3ff433b826a02b))


### Bug Fixes

* give the tiny fixture the bug its prompt describes ([#94](https://github.com/AI-for-dev/trysquare/issues/94)) ([5c0a98b](https://github.com/AI-for-dev/trysquare/commit/5c0a98bcd5f2f5b738299c84a549efd20e45f1a0))
* run the judge inside the runs' isolation backend ([#95](https://github.com/AI-for-dev/trysquare/issues/95)) ([bd04931](https://github.com/AI-for-dev/trysquare/commit/bd04931735e837ae462a24194249e28355d507ba))

## [0.3.0](https://github.com/AI-for-dev/trysquare/compare/v0.2.1...v0.3.0) (2026-10-04)


### Features

* confine each run with bubblewrap, without docker ([#91](https://github.com/AI-for-dev/trysquare/issues/91)) ([23fe50f](https://github.com/AI-for-dev/trysquare/commit/23fe50fb33ba06ae6f0627b7876375496509a77c))
* hold each container to set limits, and export sessions inside it ([#88](https://github.com/AI-for-dev/trysquare/issues/88)) ([5f0344c](https://github.com/AI-for-dev/trysquare/commit/5f0344c64c9e769eb9293131007b11e4612559c6))
* record the agent each run ran under ([#79](https://github.com/AI-for-dev/trysquare/issues/79)) ([941cbe1](https://github.com/AI-for-dev/trysquare/commit/941cbe121f525ca4955081ce5f1e1ce4a075a303))
* run each agent in its own docker container ([#86](https://github.com/AI-for-dev/trysquare/issues/86)) ([87269a9](https://github.com/AI-for-dev/trysquare/commit/87269a96b2d29a8ecb7cbf5c6afd57e7111560d8))
* run on pi 1.0 ([#82](https://github.com/AI-for-dev/trysquare/issues/82)) ([da575c6](https://github.com/AI-for-dev/trysquare/commit/da575c6a3a07834970390f90a65e2a7db3c73d4e))
* run the agent through a confinement backend ([#85](https://github.com/AI-for-dev/trysquare/issues/85)) ([7e53c43](https://github.com/AI-for-dev/trysquare/commit/7e53c434698b46a0144b3a3dac72bd0da00b052c))
* ship a pinned image for agents run in docker ([#89](https://github.com/AI-for-dev/trysquare/issues/89)) ([c70ca61](https://github.com/AI-for-dev/trysquare/commit/c70ca610c7d960d73584124082c86c784ad9e38c))


### Bug Fixes

* establish no gap from fewer than four runs a side ([#84](https://github.com/AI-for-dev/trysquare/issues/84)) ([41b08e3](https://github.com/AI-for-dev/trysquare/commit/41b08e3a7b92cfb0f120ae8bf1757e3dd35c55f5))
* keep a number on one line in the synthesis page ([#83](https://github.com/AI-for-dev/trysquare/issues/83)) ([6906a3d](https://github.com/AI-for-dev/trysquare/commit/6906a3de972921305fd593c61398645101ac55b2))
* leave nothing after the etalon in a run's clone ([#90](https://github.com/AI-for-dev/trysquare/issues/90)) ([68af319](https://github.com/AI-for-dev/trysquare/commit/68af3198c197ce2a66c7402a2a473b3d74bcb69c))

## [0.2.1](https://github.com/AI-for-dev/trysquare/compare/v0.2.0...v0.2.1) (2026-09-29)


### Bug Fixes

* a request the provider never served is no turn ([#75](https://github.com/AI-for-dev/trysquare/issues/75)) ([6edae71](https://github.com/AI-for-dev/trysquare/commit/6edae715e3a8453ac5216e67cd171f4a635d2dac))

## [0.2.0](https://github.com/AI-for-dev/trysquare/compare/v0.1.0...v0.2.0) (2026-09-28)


### Features

* adjust each star for the table it sits in ([#74](https://github.com/AI-for-dev/trysquare/issues/74)) ([c86cf85](https://github.com/AI-for-dev/trysquare/commit/c86cf85e7405128bc14ef998943b004b3f7594f3))


### Bug Fixes

* a run the provider gave up on is no measurement ([#73](https://github.com/AI-for-dev/trysquare/issues/73)) ([dc8ee8b](https://github.com/AI-for-dev/trysquare/commit/dc8ee8ba25bec6bd523b38136361ef5f4b896f66))

## 0.1.0 (2026-09-21)


### Features

* publish a release to TestPyPI ([#71](https://github.com/AI-for-dev/trysquare/issues/71)) ([9ddaef4](https://github.com/AI-for-dev/trysquare/commit/9ddaef4d14d786f1a8443fb72b3fbbb524a17ccf))
