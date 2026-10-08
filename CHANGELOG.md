# Changelog

## [0.11.0](https://github.com/AI-for-dev/trysquare/compare/v0.10.1...v0.11.0) (2026-10-08)


### Features

* say when a combo flow did not run every node it declares ([#145](https://github.com/AI-for-dev/trysquare/issues/145)) ([49388e4](https://github.com/AI-for-dev/trysquare/commit/49388e44617137079cfb26548adcef188cb2eeae))


### Bug Fixes

* mark the run's clone as a safe git directory in the sandbox home ([#144](https://github.com/AI-for-dev/trysquare/issues/144)) ([b8fef46](https://github.com/AI-for-dev/trysquare/commit/b8fef4657f7f3416d7698edc8d1a6b2ce9d9106d))

## [0.10.1](https://github.com/AI-for-dev/trysquare/compare/v0.10.0...v0.10.1) (2026-10-08)


### Bug Fixes

* show a combo /run's subagent sessions live in watch ([#141](https://github.com/AI-for-dev/trysquare/issues/141)) ([c864a8d](https://github.com/AI-for-dev/trysquare/commit/c864a8d2ff788e0960db097413fc948a0978a6dc))
* stop the relay from printing a traceback when an agent drops a connection ([#140](https://github.com/AI-for-dev/trysquare/issues/140)) ([abf7e7a](https://github.com/AI-for-dev/trysquare/commit/abf7e7a424466acb0f215a28c9d5e5c4de5811e2))

## [0.10.0](https://github.com/AI-for-dev/trysquare/compare/v0.9.0...v0.10.0) (2026-10-08)


### Features

* name each run's session and the runs' backend in live.json ([#136](https://github.com/AI-for-dev/trysquare/issues/136)) ([bdaec22](https://github.com/AI-for-dev/trysquare/commit/bdaec22d8b01db5b10d8480ffe8ec9dd532e168e))
* name the subagents a combo /run is working on in watch ([#133](https://github.com/AI-for-dev/trysquare/issues/133)) ([a09c6d4](https://github.com/AI-for-dev/trysquare/commit/a09c6d4506375fab7c8c515bcd19c9e65ad1897e))
* show a run's session live from the watch dashboard ([#137](https://github.com/AI-for-dev/trysquare/issues/137)) ([ee48eab](https://github.com/AI-for-dev/trysquare/commit/ee48eab62e91ec3b969f54f4e5883def0749f58e))


### Bug Fixes

* read an inline prompt with a slash as text, not as a path ([#135](https://github.com/AI-for-dev/trysquare/issues/135)) ([8021ce5](https://github.com/AI-for-dev/trysquare/commit/8021ce5046b96b0bb06c5166b8628b47fb5de932))
* reset the ledger as soon as --overwrite relaunches a matrix ([#138](https://github.com/AI-for-dev/trysquare/issues/138)) ([b72d395](https://github.com/AI-for-dev/trysquare/commit/b72d39542aa37aa8ff853c7fb011d1e2977672e6))

## [0.9.0](https://github.com/AI-for-dev/trysquare/compare/v0.8.1...v0.9.0) (2026-10-08)


### Features

* translate the board into English ([#130](https://github.com/AI-for-dev/trysquare/issues/130)) ([0e78441](https://github.com/AI-for-dev/trysquare/commit/0e7844154c449da0675aa76e11173f7c22a6a3f3))


### Bug Fixes

* archive a combo flow's run directory as the run's session ([#131](https://github.com/AI-for-dev/trysquare/issues/131)) ([56531a7](https://github.com/AI-for-dev/trysquare/commit/56531a7882679642f954c9f1f2614612bae58f0f))
* follow a combo /run in watch while it runs ([#128](https://github.com/AI-for-dev/trysquare/issues/128)) ([93a6078](https://github.com/AI-for-dev/trysquare/commit/93a6078e7bbaab99f02f145c449fc16e2f883928))
* show a combo flow's pages in render --html and the synthesis ([#132](https://github.com/AI-for-dev/trysquare/issues/132)) ([0614969](https://github.com/AI-for-dev/trysquare/commit/06149694276fd6ed89fa6b24315b9202a4fc993d))

## [0.8.1](https://github.com/AI-for-dev/trysquare/compare/v0.8.0...v0.8.1) (2026-10-08)


### Bug Fixes

* anchor a relative workdir to the config file ([#124](https://github.com/AI-for-dev/trysquare/issues/124)) ([ca56acf](https://github.com/AI-for-dev/trysquare/commit/ca56acf95886bdadff31489a63840ae8a6f7456e))
* drop the agent's connection when the provider cuts a stream ([#127](https://github.com/AI-for-dev/trysquare/issues/127)) ([6659604](https://github.com/AI-for-dev/trysquare/commit/6659604e8bd3d72ae3957860fc36769c4bfdd9d3))
* measure a run driven by a combo /run from the flow's own usage ([#125](https://github.com/AI-for-dev/trysquare/issues/125)) ([cb3bf0f](https://github.com/AI-for-dev/trysquare/commit/cb3bf0f33811b787b267fc3b881bf19f661bad96))
* write the [harness.agents] model override into the copied agent files ([#123](https://github.com/AI-for-dev/trysquare/issues/123)) ([f7ecfdf](https://github.com/AI-for-dev/trysquare/commit/f7ecfdf030d897c1d466d0dcd642fd36cc42b64e))

## [0.8.0](https://github.com/AI-for-dev/trysquare/compare/v0.7.0...v0.8.0) (2026-10-06)


### Features

* a GitHub Actions style matrix in [axes]: preset values, exclude and include ([#121](https://github.com/AI-for-dev/trysquare/issues/121)) ([1a75da1](https://github.com/AI-for-dev/trysquare/commit/1a75da1d66817df019c04f682029ad8c829a2190))
* add up the bricks a cell takes from several presets or axes ([#120](https://github.com/AI-for-dev/trysquare/issues/120)) ([1bd98be](https://github.com/AI-for-dev/trysquare/commit/1bd98bed556a0d97d6b6a49f6cea8ad0c982e689))
* declare shared cell lines once as named presets ([#116](https://github.com/AI-for-dev/trysquare/issues/116)) ([56a025a](https://github.com/AI-for-dev/trysquare/commit/56a025adb2f4c29c7f9e85906c5fcc285e6ad2df))


### Bug Fixes

* check that a setup script exists before the first run ([#117](https://github.com/AI-for-dev/trysquare/issues/117)) ([8400032](https://github.com/AI-for-dev/trysquare/commit/840003219f1bc2ed1e202ae3ff6824e94c23cef3))
* refuse a cell or preset key that nothing reads ([#119](https://github.com/AI-for-dev/trysquare/issues/119)) ([ba5dea9](https://github.com/AI-for-dev/trysquare/commit/ba5dea9ad4bc9db8bad3ae10061cef6610ddb190))

## [0.7.0](https://github.com/AI-for-dev/trysquare/compare/v0.6.2...v0.7.0) (2026-10-06)


### Features

* measure a cell without the project's git history ([#113](https://github.com/AI-for-dev/trysquare/issues/113)) ([4df7108](https://github.com/AI-for-dev/trysquare/commit/4df7108be34168659e94441b06f13ae06292f784))
* run a setup script in the clone before the agent starts ([#114](https://github.com/AI-for-dev/trysquare/issues/114)) ([993e518](https://github.com/AI-for-dev/trysquare/commit/993e518755fa1f9330728668f1b1e691ab591cc7))

## [0.6.2](https://github.com/AI-for-dev/trysquare/compare/v0.6.1...v0.6.2) (2026-10-06)


### Bug Fixes

* resolve the output directory so render --html accepts a relative path ([#111](https://github.com/AI-for-dev/trysquare/issues/111)) ([895fa78](https://github.com/AI-for-dev/trysquare/commit/895fa782e1ec2de11bdba01359b16ed8045b0501))
* start each launch with an empty session directory ([#110](https://github.com/AI-for-dev/trysquare/issues/110)) ([50e8e26](https://github.com/AI-for-dev/trysquare/commit/50e8e26375c7145d5396f92412987a151ab72056))

## [0.6.1](https://github.com/AI-for-dev/trysquare/compare/v0.6.0...v0.6.1) (2026-10-06)


### Documentation

* give each command of the cheat sheet its own flags ([#105](https://github.com/AI-for-dev/trysquare/issues/105)) ([af1f6cf](https://github.com/AI-for-dev/trysquare/commit/af1f6cf3d1b36f67ab60e372586f86af3d493844))
* put isolation and the ninth invariant on the cheat sheet ([#106](https://github.com/AI-for-dev/trysquare/issues/106)) ([b640223](https://github.com/AI-for-dev/trysquare/commit/b640223e63513f2b055b8aae53a40e11d1849e35))

## [0.6.0](https://github.com/AI-for-dev/trysquare/compare/v0.5.0...v0.6.0) (2026-10-06)


### Features

* check that each model answers with validate --ping ([#102](https://github.com/AI-for-dev/trysquare/issues/102)) ([bc4e0b4](https://github.com/AI-for-dev/trysquare/commit/bc4e0b4b78d6964f86af04365972566c843268e8))
* run pi inside the sandbox with trysquare pi ([#103](https://github.com/AI-for-dev/trysquare/issues/103)) ([6615eac](https://github.com/AI-for-dev/trysquare/commit/6615eac3f75430ebd346a10e5e2b4fdda8e575ea))

## [0.5.0](https://github.com/AI-for-dev/trysquare/compare/v0.4.0...v0.5.0) (2026-10-05)


### Features

* read a relayed key from a .env file ([#99](https://github.com/AI-for-dev/trysquare/issues/99)) ([c6a541d](https://github.com/AI-for-dev/trysquare/commit/c6a541d89d9a0aa3b9e116b73ed55215cfe14382))
* take the sandbox's providers from a models file of their own ([#100](https://github.com/AI-for-dev/trysquare/issues/100)) ([8f7b46c](https://github.com/AI-for-dev/trysquare/commit/8f7b46c9ba2d800282768bc752a8978a18fa4447))

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
