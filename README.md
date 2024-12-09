This repository contains scripts for automatically building the Python
documentation on [docs.python.org](https://docs.python.org).


## How to test it?

The following command should build all maintained versions and
translations in `./www`, beware it can take a few hours:

```shell
python3 ./build_docs.py --quick --build-root ./build_root --www-root ./www --log-directory ./logs --group $(id -g) --skip-cache-invalidation
```

If you don't need to build all translations of all branches, add
`--language en --branch main`.


## Check current version

Install `tools_requirements.txt` then run `python check_versions.py
../cpython/` (pointing to a real CPython clone) to see which version
of Sphinx we're using where:

    Sphinx configuration in various branches:

    =========  =============  ==================  ====================
    version    travis         requirements.txt    conf.py
    =========  =============  ==================  ====================
    2.7        sphinx~=2.0.1  ø                   needs_sphinx='1.2'
    3.5        sphinx==1.8.2  ø                   needs_sphinx='1.8'
    3.6        sphinx==1.8.2  ø                   needs_sphinx='1.2'
    3.7        sphinx==1.8.2  sphinx==2.3.1       needs_sphinx="1.6.6"
    3.8        ø              sphinx==2.4.4       needs_sphinx='1.8'
    3.9        ø              sphinx==2.4.4       needs_sphinx='1.8'
    3.10       ø              sphinx==3.4.3       needs_sphinx='3.2'
    3.11       ø              sphinx~=7.2.0       needs_sphinx='4.2'
    3.12       ø              sphinx~=8.1.0       needs_sphinx='7.2.6'
    3.13       ø              sphinx~=8.1.0       needs_sphinx='7.2.6'
    3.14       ø              sphinx~=8.1.0       needs_sphinx='7.2.6'
    =========  =============  ==================  ====================

    Sphinx build as seen on docs.python.org:

    =========  =====  =====  =====  =====  =====  =====  =====  =====  =======  =====  =====  =======  =======
    version    en     es     fr     id     it     ja     ko     pl     pt-br    tr     uk     zh-cn    zh-tw
    =========  =====  =====  =====  =====  =====  =====  =====  =====  =======  =====  =====  =======  =======
    3.9        2.4.4  2.4.4  2.4.4  2.4.4  2.4.4  2.4.4  2.4.4  2.4.4  2.4.4    2.4.4  2.4.4  2.4.4    2.4.4
    3.10       3.4.3  3.4.3  3.4.3  3.4.3  3.4.3  3.4.3  3.4.3  3.4.3  3.4.3    3.4.3  3.4.3  3.4.3    3.4.3
    3.11       7.2.6  7.2.6  7.2.6  7.2.6  7.2.6  7.2.6  7.2.6  7.2.6  7.2.6    7.2.6  7.2.6  7.2.6    7.2.6
    3.12       8.1.3  8.1.3  8.1.3  8.1.3  8.1.3  8.1.3  8.1.3  8.1.3  8.1.3    8.1.3  8.1.3  8.1.3    8.1.3
    3.13       8.1.3  8.1.3  8.1.3  8.1.3  8.1.3  8.1.3  8.1.3  8.1.3  8.1.3    8.1.3  8.1.3  8.1.3    8.1.3
    3.14       8.1.3  8.1.3  8.1.3  8.1.3  8.1.3  8.1.3  8.1.3  8.1.3  8.1.3    8.1.3  8.1.3  8.1.3    8.1.3
    =========  =====  =====  =====  =====  =====  =====  =====  =====  =======  =====  =====  =======  =======
