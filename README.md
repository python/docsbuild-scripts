This repository contains scripts for automatically building the Python
documentation on [docs.python.org](https://docs.python.org).

# How to test it?

    $ mkdir -p www logs build_root
    $ python3 -m venv build_root/venv/
    $ build_root/venv/bin/python -m pip install -r requirements.txt
    $ python3 ./build_docs.py --quick --build-root build_root --www-root www --log-directory logs --group $(id -g)
