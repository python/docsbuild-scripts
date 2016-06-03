#!/usr/bin/env python3

# Runs a build of the Python docs for various branches.
#
# Usages:
#
#   dailybuild.py [-q]
#
# without any arguments builds docs for all branches configured in the global
# BRANCHES value. -q selects "quick build", which means to build only HTML.
#
#   dailybuild.py [-q] [-d] <checkout> <target>
#
# builds one version, where <checkout> is a HG checkout directory of the Python
# branch to build docs for, and <target> is the directory where the result
# should be placed. If -d is given, the docs are built even if the branch is in
# development mode (i.e. version contains a, b or c).
#
# This script was originally created and by Georg Brandl in March 2010. Modified
# by Benjamin Peterson to do CDN cache invalidation.

import getopt
import logging
import os
import subprocess
import sys


BUILDROOT = "/srv/docsbuild"
SPHINXBUILD = os.path.join(BUILDROOT, "environment/bin/sphinx-build")
WWWROOT = "/srv/docs.python.org"

BRANCHES = [
    # checkout, target, isdev
    (BUILDROOT + "/python34", WWWROOT + "/3.4", False),
    (BUILDROOT + "/python35", WWWROOT + "/3.5", False),
    (BUILDROOT + "/python36", WWWROOT + "/3.6", True),
    (BUILDROOT + "/python27", WWWROOT + "/2.7", False),
]
DEVGUIDE_CHECKOUT = BUILDROOT + "/devguide"
DEVGUIDE_TARGET = WWWROOT + "/devguide"


def _file_unchanged(old, new):
    with open(old, "rb") as fp1, open(new, "rb") as fp2:
        st1 = os.fstat(fp1.fileno())
        st2 = os.fstat(fp2.fileno())
        if st1.st_size != st2.st_size:
            return False
        if st1.st_mtime >= st2.st_mtime:
            return True
        while True:
            one = fp1.read(4096)
            two = fp2.read(4096)
            if one != two:
                return False
            if one == b"":
                break
    return True

def shell_out(cmd):
    logging.debug("Running command %r", cmd)
    try:
        subprocess.check_output(cmd, shell=True, stdin=subprocess.PIPE, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        logging.error("command failed with output %r", e.output.decode("utf-8"))
        raise

def build_one(checkout, target, isdev, quick):
    logging.info("Doc autobuild started in %s", checkout)
    os.chdir(checkout)

    logging.info("Updating checkout")
    shell_out("hg pull -u")

    maketarget = "autobuild-" + ("html" if quick else ("dev" if isdev else "stable"))
    logging.info("Running make %s", maketarget)
    logname = os.path.basename(checkout) + ".log"
    shell_out("cd Doc; make SPHINXBUILD=%s %s >> /var/log/docsbuild/%s 2>&1" %
              (SPHINXBUILD, maketarget, logname))

    logging.info("Computing changed files")
    changed = []
    for dirpath, dirnames, filenames in os.walk("Doc/build/html/"):
        dir_rel = dirpath[len("Doc/build/html/"):]
        for fn in filenames:
            local_path = os.path.join(dirpath, fn)
            rel_path = os.path.join(dir_rel, fn)
            target_path = os.path.join(target, rel_path)
            if (os.path.exists(target_path) and
                not _file_unchanged(target_path, local_path)):
                changed.append(rel_path)

    logging.info("Copying HTML files to %s", target)
    shell_out("chown -R :docs Doc/build/html/")
    shell_out("chmod -R o+r Doc/build/html/")
    shell_out("find Doc/build/html/ -type d -exec chmod o+x {} ';'")
    shell_out("cp -a Doc/build/html/* %s" % target)
    if not quick:
        logging.debug("Copying dist files")
        shell_out("chown -R :docs Doc/dist/")
        shell_out("chmod -R o+r Doc/dist/")
        shell_out("mkdir -m o+rx -p %s/archives" % target)
        shell_out("chown :docs %s/archives" % target)
        shell_out("cp -a Doc/dist/* %s/archives" % target)
        changed.append("archives/")
        for fn in os.listdir(os.path.join(target, "archives")):
            changed.append("archives/" + fn)

    logging.info("%s files changed", len(changed))
    if changed:
        target_ino = os.stat(target).st_ino
        targets_dir = os.path.dirname(target)
        prefixes = []
        for fn in os.listdir(targets_dir):
            if os.stat(os.path.join(targets_dir, fn)).st_ino == target_ino:
                prefixes.append(fn)
        to_purge = []
        for prefix in prefixes:
            to_purge.extend(prefix + "/" + p for p in changed)
        logging.info("Running CDN purge")
        shell_out("curl -X PURGE \"https://docs.python.org/{%s}\"" % ",".join(to_purge))

    logging.info("Finished %s", checkout)

def build_devguide():
    logging.info("Building devguide")
    shell_out("git -C %s pull" % (DEVGUIDE_CHECKOUT,))
    shell_out("%s %s %s" % (SPHINXBUILD, DEVGUIDE_CHECKOUT, DEVGUIDE_TARGET))
    shell_out("chmod -R o+r %s" % (DEVGUIDE_TARGET,))
    # TODO Do Fastly invalidation.

def usage():
    print("Usage:")
    print("  {} (to build all branches)".format(sys.argv[0]))
    print("or")
    print("  {} [-d] <checkout> <target>".format(sys.argv[0]))
    sys.exit(2)


if __name__ == '__main__':
    if sys.stderr.isatty():
        logging.basicConfig(format="%(levelname)s:%(message)s",
                            stream=sys.stderr)
    else:
        logging.basicConfig(format="%(levelname)s:%(asctime)s:%(message)s",
                            filename="/var/log/docsbuild/docsbuild.log")
    logging.root.setLevel(logging.DEBUG)

    try:
        opts, args = getopt.getopt(sys.argv[1:], "dq")
    except getopt.error:
        usage()
    quick = devel = False
    for opt, _ in opts:
        if opt == "-q":
            quick = True
        if opt == "-d":
            devel = True
    if devel and not args:
        usage()
    try:
        if args:
            if len(args) != 2:
                usage()
            build_one(os.path.abspath(args[0]), os.path.abspath(args[1]), devel, quick)
        else:
            for checkout, dest, devel in BRANCHES:
                build_one(checkout, dest, devel, quick)
            build_devguide()
    except Exception:
        logging.exception("docs build raised exception")
