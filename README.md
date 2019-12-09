This repository contains scripts for automatically building the Python
documentation on [docs.python.org](https://docs.python.org).

# How to test it?

    $ mkdir -p www logs build_root
    $ python3 -m venv build_root/venv/
    $ build_root/venv/bin/python -m pip install -r requirements.txt
    $ python3 ./build_docs.py --quick --build-root build_root --www-root www --log-directory logs --group $(id -g) --skip-cache-invalidation


# Check current version

Install `tools-requirements.txt` then run ``python check_versions.py
../cpython/`` (pointing to a real cpython clone) to see which version
of Sphinx we're using where::

    Docs build server is configured to use sphinx==2.0.1

    Sphinx configuration in various branches:

    ========  =============  =============  ==================  ====================  =============  ===============
    branch    travis         azure          requirements.txt    conf.py               Makefile       Mac installer
    ========  =============  =============  ==================  ====================  =============  ===============
    2.7       sphinx~=2.0.1  ø              ø                   needs_sphinx='1.2'
    3.7       sphinx==1.8.2  sphinx==1.8.2  ø                   needs_sphinx="1.6.6"
    3.8       sphinx==1.8.2  sphinx==1.8.2  ø                   needs_sphinx='1.8'                   Sphinx==2.0.1
    master    sphinx==2.2.0  sphinx==2.2.0  sphinx==2.2.0       needs_sphinx='1.8'    Sphinx==2.2.0  Sphinx==2.2.0
    ========  =============  =============  ==================  ====================  =============  ===============

    Sphinx build as seen on docs.python.org:

    ========  =====  =====  =====  =====  =======  =======  =======  =====
      branch  en     fr     ja     ko     pt-br    zh-cn    zh-tw    id
    ========  =====  =====  =====  =====  =======  =======  =======  =====
         2.7  2.0.1  2.0.1  2.0.1  2.0.1  2.0.1    2.0.1    2.0.1    2.0.1
         3.7  2.0.1  2.0.1  2.0.1  2.0.1  2.0.1    2.0.1    2.0.1    2.0.1
         3.8  2.0.1  2.0.1  2.0.1  2.0.1  2.0.1    2.0.1    2.0.1    2.0.1
         3.9  2.0.1  2.0.1  2.0.1  2.0.1  2.0.1    2.0.1    2.0.1    2.0.1
    ========  =====  =====  =====  =====  =======  =======  =======  =====
