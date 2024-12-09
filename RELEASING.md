# Manually rebuild a branch

Docs for [feature and bugfix branches](https://devguide.python.org/versions/) are
automatically built from a cron.

Manual rebuilds are needed for new security releases,
and to add the end-of-life banner for newly end-of-life branches.

To manually rebuild a branch, for example 3.11:

```shell
ssh docs.nyc1.psf.io
sudo su --shell=/bin/bash docsbuild
screen -DUR  # Rejoin screen session if it exists, otherwise create a new one
/srv/docsbuild/venv/bin/python /srv/docsbuild/scripts/build_docs.py --branch 3.11
```
