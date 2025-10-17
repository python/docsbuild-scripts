# docsbuild-scripts

[![GitHub Actions status](https://github.com/python/docsbuild-scripts/actions/workflows/test.yml/badge.svg)](https://github.com/python/docsbuild-scripts/actions/workflows/test.yml)
[![Codecov](https://codecov.io/gh/python/docsbuild-scripts/branch/main/graph/badge.svg)](https://codecov.io/gh/python/docsbuild-scripts)

This repository contains scripts for automatically building the Python
documentation on [docs.python.org](https://docs.python.org).


## How to test it?

The following command should build all maintained versions and
translations in `./www`, beware it can take a few hours:

```shell
python3 ./build_docs.py --quick --build-root ./build_root --www-root ./www --log-directory ./logs --group $(id -g) --skip-cache-invalidation
```

If you don't need to build all translations of all branches, add
`--languages en --branches main`.


## Sphinx versions

<!-- [[[cog
from check_versions import check_versions
check_versions("../cpython/")
]]] -->
Sphinx configuration in various branches:

| version   | requirements.txt   | conf.py              |
|-----------|--------------------|----------------------|
| 2.6       | ø                  | ø                    |
| 2.7       | ø                  | ø                    |
| 3.0       | ø                  | ø                    |
| 3.1       | ø                  | ø                    |
| 3.2       | ø                  | ø                    |
| 3.3       | ø                  | ø                    |
| 3.4       | ø                  | needs_sphinx='1.2'   |
| 3.5       | ø                  | ø                    |
| 3.6       | ø                  | ø                    |
| 3.7       | ø                  | ø                    |
| 3.8       | ø                  | ø                    |
| 3.9       | sphinx==2.4.4      | needs_sphinx='1.8'   |
| 3.10      | sphinx==3.4.3      | needs_sphinx='3.2'   |
| 3.11      | sphinx~=7.2.0      | needs_sphinx='4.2'   |
| 3.12      | sphinx~=8.2.0      | needs_sphinx='8.2.0' |
| 3.13      | sphinx~=8.2.0      | needs_sphinx='8.2.0' |
| 3.14      | sphinx~=8.2.0      | needs_sphinx='8.2.0' |
| 3.15      | sphinx~=8.2.0      | needs_sphinx='8.2.0' |

Sphinx build as seen on docs.python.org:

| version   | el    | en    | es    | fr    | bn-in   | id    | it    | ja    | ko    | pl    | pt-br   | ro    | tr    | uk    | zh-cn   | zh-tw   |
|-----------|-------|-------|-------|-------|---------|-------|-------|-------|-------|-------|---------|-------|-------|-------|---------|---------|
| 2.6       | ø     | 0.6.5 | ø     | ø     | ø       | ø     | ø     | ø     | ø     | ø     | ø       | ø     | ø     | ø     | ø       | ø       |
| 2.7       | ø     | 2.3.1 | ø     | 2.3.1 | ø       | 2.3.1 | ø     | 2.3.1 | 2.3.1 | ø     | 2.3.1   | ø     | ø     | ø     | 2.3.1   | 2.3.1   |
| 3.0       | ø     | 0.6   | ø     | ø     | ø       | ø     | ø     | ø     | ø     | ø     | ø       | ø     | ø     | ø     | ø       | ø       |
| 3.1       | ø     | 0.6.5 | ø     | ø     | ø       | ø     | ø     | ø     | ø     | ø     | ø       | ø     | ø     | ø     | ø       | ø       |
| 3.2       | ø     | 1.0.7 | ø     | ø     | ø       | ø     | ø     | ø     | ø     | ø     | ø       | ø     | ø     | ø     | ø       | ø       |
| 3.3       | ø     | 1.2   | ø     | ø     | ø       | ø     | ø     | ø     | ø     | ø     | ø       | ø     | ø     | ø     | ø       | ø       |
| 3.4       | ø     | 1.2.3 | ø     | ø     | ø       | ø     | ø     | ø     | ø     | ø     | ø       | ø     | ø     | ø     | ø       | ø       |
| 3.5       | ø     | 1.8.4 | 1.8.4 | 1.8.4 | ø       | 1.8.4 | ø     | 1.8.4 | 1.8.4 | 1.8.4 | 1.8.4   | ø     | ø     | ø     | 1.8.4   | 1.8.4   |
| 3.6       | ø     | 2.3.1 | 2.3.1 | 2.3.1 | ø       | 2.3.1 | ø     | 2.3.1 | 2.3.1 | 2.3.1 | 2.3.1   | ø     | ø     | ø     | 2.3.1   | 2.3.1   |
| 3.7       | ø     | 2.3.1 | 2.3.1 | 2.3.1 | ø       | 2.3.1 | 2.3.1 | 2.3.1 | 2.3.1 | 2.3.1 | 2.3.1   | ø     | 2.3.1 | 2.3.1 | 2.3.1   | 2.3.1   |
| 3.8       | ø     | 2.4.4 | 2.4.4 | 2.4.4 | ø       | 2.4.4 | 2.4.4 | 2.4.4 | 2.4.4 | 2.4.4 | 2.4.4   | ø     | 2.4.4 | 2.4.4 | 2.4.4   | 2.4.4   |
| 3.9       | 2.4.4 | 2.4.4 | 2.4.4 | 2.4.4 | 2.4.4   | 2.4.4 | 2.4.4 | 2.4.4 | 2.4.4 | 2.4.4 | 2.4.4   | 2.4.4 | 2.4.4 | 2.4.4 | 2.4.4   | 2.4.4   |
| 3.10      | 3.4.3 | 3.4.3 | 3.4.3 | 3.4.3 | 3.4.3   | 3.4.3 | 3.4.3 | 3.4.3 | 3.4.3 | 3.4.3 | 3.4.3   | 3.4.3 | 3.4.3 | 3.4.3 | 3.4.3   | 3.4.3   |
| 3.11      | 7.2.6 | 7.2.6 | 7.2.6 | 7.2.6 | 7.2.6   | 7.2.6 | 7.2.6 | 7.2.6 | 7.2.6 | 7.2.6 | 7.2.6   | 7.2.6 | 7.2.6 | 7.2.6 | 7.2.6   | 7.2.6   |
| 3.12      | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3   | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3   | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3   | 8.2.3   |
| 3.13      | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3   | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3   | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3   | 8.2.3   |
| 3.14      | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3   | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3   | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3   | 8.2.3   |
| 3.15      | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3   | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3   | 8.2.3 | 8.2.3 | 8.2.3 | 8.2.3   | 8.2.3   |
<!-- [[[end]]] -->

Install `tools_requirements.txt` then run `python check_versions.py
../cpython/` (pointing to a real CPython clone) to see which versions
of Sphinx we're using.

Or run `tox -e cog` (with a clone at `../cpython`) to directly update these tables.

## Manually rebuild a branch

Docs for [feature and bugfix branches](https://devguide.python.org/versions/) are
automatically built from a cron.

Manual rebuilds are needed for new security releases,
and to add the end-of-life banner for newly end-of-life branches.

To manually rebuild a branch, for example 3.11:

```shell
ssh docs.nyc1.psf.io
sudo su --shell=/bin/bash docsbuild
screen -DUR  # Rejoin screen session if it exists, otherwise create a new one
/srv/docsbuild/venv/bin/python /srv/docsbuild/scripts/build_docs.py --force --branches 3.11
```
