This repository contains scripts for automatically building the Python
documentation on [docs.python.org](https://docs.python.org).


# How to test it?

The following command should build all maintained versions and
translations in `./www`, beware it can take a few hours:

```shell
python3 ./build_docs.py --quick --build-root ./build_root --www-root ./www --log-directory ./logs --group $(id -g) --skip-cache-invalidation
```

If you don't need to build all translations of all branches, add
`--language en --branch main`.


# Check current version

Install `tools_requirements.txt` then run `python check_versions.py
../cpython/` (pointing to a real CPython clone) to see which version
of Sphinx we're using where:

    Sphinx configuration in various branches:

    =========  =============  =============  ==================  ====================
      version  travis         azure          requirements.txt    conf.py
    =========  =============  =============  ==================  ====================
         2.7   ø              ø              ø                   ø
         3.5   ø              ø              ø                   ø
         3.6   ø              ø              ø                   ø
         3.7   sphinx==1.8.2  sphinx==1.8.2  sphinx==2.3.1       needs_sphinx="1.6.6"
         3.8   ø              ø              sphinx==2.4.4       needs_sphinx='1.8'
         3.9   ø              ø              sphinx==2.4.4       needs_sphinx='1.8'
         3.1   ø              ø              sphinx==3.4.3       needs_sphinx='3.2'
         3.11  ø              ø              sphinx==4.5.0       needs_sphinx='3.2'
         3.12  ø              ø              sphinx==4.5.0       needs_sphinx='3.2'
         3.13  ø              ø              sphinx==6.2.0       needs_sphinx='3.2'
    =========  =============  =============  ==================  ====================

    Sphinx build as seen on docs.python.org:

    =========  =====  =====  =====  =====  =====  =====  =====  =====  =======  =====  =====  =======  =======
    version    en     es     fr     id     it     ja     ko     pl     pt-br    tr     uk     zh-cn    zh-tw
    =========  =====  =====  =====  =====  =====  =====  =====  =====  =======  =====  =====  =======  =======
    2.7        2.3.1  2.3.1  2.3.1  ø      2.3.1  2.3.1  2.3.1  ø      2.3.1    ø      2.3.1  ø        ø
    3.5        1.8.4  1.8.4  1.8.4  ø      1.8.4  1.8.4  1.8.4  1.8.4  1.8.4    1.8.4  1.8.4  ø        ø
    3.6        2.3.1  2.3.1  2.3.1  ø      2.3.1  2.3.1  2.3.1  2.3.1  2.3.1    2.3.1  2.3.1  ø        ø
    3.7        2.3.1  2.3.1  2.3.1  2.3.1  2.3.1  2.3.1  2.3.1  2.3.1  2.3.1    2.3.1  2.3.1  2.3.1    ø
    3.8        2.4.4  2.4.4  2.4.4  2.4.4  2.4.4  2.4.4  2.4.4  2.4.4  2.4.4    2.4.4  2.4.4  2.4.4    ø
    3.9        2.4.4  2.4.4  2.4.4  2.4.4  2.4.4  2.4.4  2.4.4  2.4.4  2.4.4    2.4.4  2.4.4  2.4.4    2.4.4
    3.10       3.4.3  3.4.3  3.4.3  3.4.3  3.4.3  3.4.3  3.4.3  3.4.3  3.4.3    3.4.3  3.4.3  3.4.3    3.4.3
    3.11       4.5.0  4.5.0  4.5.0  4.5.0  4.5.0  4.5.0  4.5.0  4.5.0  4.5.0    4.5.0  4.5.0  4.5.0    4.5.0
    3.12       4.5.0  4.5.0  4.5.0  4.5.0  4.5.0  4.5.0  4.5.0  4.5.0  4.5.0    4.5.0  4.5.0  4.5.0    4.5.0
    3.13       6.2.0  6.2.0  6.2.0  6.2.0  6.2.0  4.5.0  6.2.0  6.2.0  6.2.0    6.2.0  6.2.0  6.2.0    4.5.0
    =========  =====  =====  =====  =====  =====  =====  =====  =====  =======  =====  =====  =======  =======

## The GitHub hook server

`build_docs_server.py` is a simple HTTP server handling GitHub Webhooks
requests to build the doc when needed. It only needs `push` events.

Its logging can be configured by giving a yaml file path to the
`--logging-config` argument.

By default the loglevel is `DEBUG` on `stderr`, the default config can
be found in the code so one can bootstrap a different config from it.
