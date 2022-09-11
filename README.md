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

    ========  =============  =============  ==================  ====================  =============  ===============
    version   travis         azure          requirements.txt    conf.py               Makefile       Mac installer
    ========  =============  =============  ==================  ====================  =============  ===============
        2.7   sphinx~=2.0.1  ø              ø                   needs_sphinx='1.2'    ø              ø
        3.5   sphinx==1.8.2  ø              ø                   needs_sphinx='1.8'    ø              ø
        3.6   sphinx==1.8.2  sphinx==1.8.2  ø                   needs_sphinx='1.2'    Sphinx==2.3.1  ø
        3.7   sphinx==1.8.2  sphinx==1.8.2  sphinx==2.3.1       needs_sphinx="1.6.6"  ø              Sphinx==2.3.1
        3.8   ø              ø              sphinx==2.4.4       needs_sphinx='1.8'    ø              ø
        3.9   ø              ø              sphinx==2.4.4       needs_sphinx='1.8'    ø              ø
        3.10  ø              ø              sphinx==3.4.3       needs_sphinx='3.2'    ø              ø
        3.11  ø              ø              sphinx==4.5.0       needs_sphinx='3.2'    ø              ø
        3.12  ø              ø              sphinx==4.5.0       needs_sphinx='3.2'    ø              ø
    ========  =============  =============  ==================  ====================  =============  ===============

    Sphinx build as seen on docs.python.org:

    ========  =====  =====  =====  =====  =====  =====  =====  =======  =======  =======
    version   en     es     fr     id     ja     ko     pl     pt-br    zh-cn    zh-tw
    ========  =====  =====  =====  =====  =====  =====  =====  =======  =======  =======
    2.7       2.3.1  2.3.1  2.3.1  2.3.1  2.3.1  2.3.1  2.3.1  2.3.1    2.3.1    2.3.1
    3.5       1.8.4  1.8.4  1.8.4  1.8.4  1.8.4  1.8.4  1.8.4  1.8.4    1.8.4    1.8.4
    3.6       2.3.1  2.3.1  2.3.1  2.3.1  2.3.1  2.3.1  2.3.1  2.3.1    2.3.1    2.3.1
    3.7       2.3.1  2.3.1  2.3.1  2.3.1  2.3.1  2.3.1  2.3.1  2.3.1    2.3.1    2.3.1
    3.8       2.4.4  2.4.4  2.4.4  2.4.4  2.4.4  2.4.4  2.4.4  2.4.4    2.4.4    2.4.4
    3.9       2.4.4  2.4.4  2.4.4  2.4.4  2.4.4  2.4.4  2.4.4  2.4.4    2.4.4    2.4.4
    3.10      3.4.3  3.4.3  3.4.3  3.4.3  3.4.3  3.4.3  3.4.3  3.4.3    3.4.3    3.4.3
    3.11      4.5.0  4.5.0  4.5.0  4.5.0  4.5.0  4.5.0  4.5.0  4.5.0    4.5.0    4.5.0
    3.12      4.5.0  4.5.0  4.5.0  4.5.0  4.5.0  4.5.0  4.5.0  4.5.0    4.5.0    4.5.0
    ========  =====  =====  =====  =====  =====  =====  =====  =======  =======  =======


## The github hook server

`build_docs_server.py` is a simple HTTP server handling Github Webhooks
requests to build the doc when needed. It only needs `push` events.

Its logging can be configured by giving a yaml file path to the
`--logging-config` argument.

By default the loglevel is `DEBUG` on `stderr`, the default config can
be found in the code so one can bootstrap a different config from it.
