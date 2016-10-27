#!/usr/bin/env python3

# Runs a build of the Python docs for various branches.
#
# Usage:
#
#   build_docs.py [-h] [-d] [-q] [-b 3.6] [-r BUILD_ROOT] [-w WWW_ROOT]
#                 [--devguide-checkout DEVGUIDE_CHECKOUT]
#                 [--devguide-target DEVGUIDE_TARGET]
#
# Without any arguments builds docs for all branches configured in the
# global BRANCHES value, ignoring the -d flag as it's given in the
# BRANCHES configuration.
#
# -q selects "quick build", which means to build only HTML.
#
# -d allow the docs to be built even if the branch is in
# development mode (i.e. version contains a, b or c).
#
# This script was originally created and by Georg Brandl in March 2010. Modified
# by Benjamin Peterson to do CDN cache invalidation.

import getopt
import logging
import os
import subprocess
import sys


BRANCHES = [
    # version, isdev
    (3.5, False),
    (3.6, True),
    (3.7, True),
    (2.7, False)
]


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


def build_one(version, isdev, quick, sphinxbuild, build_root, www_root,
              skip_cache_invalidation=False, group='docs'):
    checkout = build_root + "/python" + str(version).replace('.', '')
    target = www_root + "/" + str(version)
    logging.info("Doc autobuild started in %s", checkout)
    os.chdir(checkout)

    logging.info("Updating checkout")
    shell_out("hg pull -u")

    maketarget = "autobuild-" + ("dev" if isdev else "stable") + ("-html" if quick else "")
    logging.info("Running make %s", maketarget)
    logname = os.path.basename(checkout) + ".log"
    shell_out("cd Doc; make SPHINXBUILD=%s %s >> /var/log/docsbuild/%s 2>&1" %
              (sphinxbuild, maketarget, logname))

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
    shell_out("chown -R :{} Doc/build/html/".format(group))
    shell_out("chmod -R o+r Doc/build/html/")
    shell_out("find Doc/build/html/ -type d -exec chmod o+x {} ';'")
    shell_out("cp -a Doc/build/html/* %s" % target)
    if not quick:
        logging.debug("Copying dist files")
        shell_out("chown -R :{} Doc/dist/".format(group))
        shell_out("chmod -R o+r Doc/dist/")
        shell_out("mkdir -m o+rx -p %s/archives" % target)
        shell_out("chown :{} {}/archives".format(group, target))
        shell_out("cp -a Doc/dist/* %s/archives" % target)
        changed.append("archives/")
        for fn in os.listdir(os.path.join(target, "archives")):
            changed.append("archives/" + fn)

    logging.info("%s files changed", len(changed))
    if changed and not skip_cache_invalidation:
        target_ino = os.stat(target).st_ino
        targets_dir = os.path.dirname(target)
        prefixes = []
        for fn in os.listdir(targets_dir):
            if os.stat(os.path.join(targets_dir, fn)).st_ino == target_ino:
                prefixes.append(fn)
        to_purge = prefixes[:]
        for prefix in prefixes:
            to_purge.extend(prefix + "/" + p for p in changed)
        logging.info("Running CDN purge")
        shell_out("curl -X PURGE \"https://docs.python.org/{%s}\"" % ",".join(to_purge))

    logging.info("Finished %s", checkout)


def build_devguide(devguide_checkout, devguide_target, sphinxbuild,
                   skip_cache_invalidation=False):
    logging.info("Building devguide")
    shell_out("git -C %s pull" % (devguide_checkout,))
    shell_out("%s %s %s" % (sphinxbuild, devguide_checkout, devguide_target))
    shell_out("chmod -R o+r %s" % (devguide_target,))
    if not skip_cache_invalidation:
        # TODO Do Fastly invalidation.
        pass


def parse_args():
    from argparse import ArgumentParser
    parser = ArgumentParser(
        description="Runs a build of the Python docs for various branches.")
    parser.add_argument(
        "-d", "--devel",
        action="store_true",
        help="Use make autobuild-dev instead of autobuild-stable")
    parser.add_argument(
        "-q", "--quick",
        action="store_true",
        help="Make HTML files only (Makefile rules suffixed with -html).")
    parser.add_argument(
        "-b", "--branch",
        metavar=3.6,
        type=float,
        help="Version to build (defaults to all maintained branches).")
    parser.add_argument(
        "-r", "--build-root",
        help="Path to a directory containing a checkout per branch.",
        default="/srv/docsbuild")
    parser.add_argument(
        "-w", "--www-root",
        help="Path where generated files will be copied.",
        default="/srv/docs.python.org")
    parser.add_argument(
        "--devguide-checkout",
        help="Path to a devguide checkout.",
        default="/srv/docsbuild/devguide")
    parser.add_argument(
        "--devguide-target",
        help="Path where the generated devguide should be copied.",
        default="/srv/docs.python.org/devguide")
    parser.add_argument(
        "--skip-cache-invalidation",
        help="Skip fastly cache invalidation.",
        action="store_true")
    parser.add_argument(
        "--group",
        help="Group files on targets and www-root file should get.",
        default="docs")
    return parser.parse_args()


if __name__ == '__main__':
    if sys.stderr.isatty():
        logging.basicConfig(format="%(levelname)s:%(message)s",
                            stream=sys.stderr)
    else:
        logging.basicConfig(format="%(levelname)s:%(asctime)s:%(message)s",
                            filename="/var/log/docsbuild/docsbuild.log")
    logging.root.setLevel(logging.DEBUG)
    args = parse_args()
    sphinxbuild = os.path.join(args.build_root, "environment/bin/sphinx-build")
    try:
        if args.branch:
            build_one(args.branch, args.devel, args.quick, sphinxbuild,
                      args.build_root, args.www_root,
                      args.skip_cache_invalidation,
                      args.group)
        else:
            for version, devel in BRANCHES:
                build_one(version, devel, args.quick, sphinxbuild,
                          args.build_root, args.www_root,
                          args.skip_cache_invalidation, args.group)
            build_devguide(args.devguide_checkout, args.devguide_target,
                           sphinxbuild, args.skip_cache_invalidation)
    except Exception:
        logging.exception("docs build raised exception")
