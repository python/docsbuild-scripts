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
import shutil
from collections import namedtuple


CPYTHON_GIT = "https://github.com/python/cpython.git"
CPYTHON_HG = "https://hg.python.org/cpython"

BRANCHES = [
    # version, branch, isdev
    (3.5, '3.5', False),
    (3.6, '3.6', True),
    (3.7, 'master', True),
    (2.7, '2.7', False)
]

Repository = namedtuple('Repository', 'name url')
Translation = namedtuple('Translation', 'lang repo branch po_path')

AFPY = Repository('afpy', 'https://github.com/AFPy/python_doc_fr.git')
JA_35 = Repository('ja_3.5', 'https://github.com/python-doc-ja/py35-locale.git')
JA_27 = Repository('ja_2.7', 'https://github.com/python-doc-ja/py27-locale.git')
TW = Repository('tw', 'https://github.com/python-doc-tw/cpython-tw.git')

# Only use lowercased tranlation names, with dash (no underscore) for
# URL consistency, as the name is used in the docs.python.org URL.
I18N_CONF = [
    {  # French
        'default': Translation('fr', AFPY, 'master', '3.6'),
        3.5: Translation('fr', AFPY, 'master', '3.5'),
        2.7: Translation('fr', AFPY, 'master', '2.7'),
    },
    {  # Japanese
        'default': Translation('ja', JA_35, 'master', 'ja/LC_MESSAGES'),
        2.7: Translation('ja', JA_27, 'master', 'ja/LC_MESSAGES'),
    },
    {  # Chienese as spoken in Taiwan
        'default': Translation('zh', TW, 'tw-3.5', 'Doc/locale/zh_Hant/LC_MESSAGES'),
    }
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


def changed_files(directory, other):
    logging.info("Computing changed files")
    changed = []
    if directory[-1] != '/':
        directory += '/'
    for dirpath, dirnames, filenames in os.walk(directory):
        dir_rel = dirpath[len(directory):]
        for fn in filenames:
            local_path = os.path.join(dirpath, fn)
            rel_path = os.path.join(dir_rel, fn)
            target_path = os.path.join(other, rel_path)
            if (os.path.exists(target_path) and
                not _file_unchanged(target_path, local_path)):
                changed.append(rel_path)
    return changed


def update_repo(dest, branch, git=False):
    logging.info("Updating repository %s", dest)
    if git:
        shell_out("git -C {} reset --hard HEAD".format(dest))
        shell_out("git -C {} checkout {}".format(dest, branch))
        shell_out("git -C {} pull --ff-only".format(dest))
    else:
        shell_out("hg --cwd {} pull -u".format(dest))


def clone_repo(url, dest, branch, git=False):
    """This function will remove a clone if the update fails, and re-clone
    it from scratch.

    This mean that switching from hg to git and vice-versa
    is now a seamless operation.
    """
    vcs = 'git' if git else 'hg'
    try:
        update_repo(dest, branch, git)
    except subprocess.CalledProcessError:
        if os.path.exists(dest):
            shutil.rmtree(dest)
        logging.info("Cloning %s", url)
        os.makedirs(dest, mode=0o775)
        shell_out("{} clone {} {}".format(vcs, url, dest))
        if git:
            shell_out("git -C {} checkout {}".format(dest, branch))
        else:
            shell_out("hg --cwd {} checkout {}".format(dest, branch))


def build_one(version, branch, isdev, quick, sphinxbuild, build_root, www_root,
              translation=None,
              skip_cache_invalidation=False, group='docs', git=False,
              log_directory='/var/log/docsbuild/'):
    os.makedirs(log_directory, mode=0o750, exist_ok=True)
    checkout = build_root + "/python" + str(version).replace('.', '')
    target = www_root + "/" + (translation.lang + "/" if translation else "") + str(version)
    logging.info("Doc autobuild started in %s", checkout)
    clone_repo(CPYTHON_GIT if git else CPYTHON_HG,
               checkout, branch, git)
    os.chdir(checkout)
    maketarget = "autobuild-" + ("dev" if isdev else "stable") + ("-html" if quick else "")
    sphinxopts = ""
    if translation:
        sphinxopts = 'SPHINXOPTS="-D gettext_compact=0 -D locale_dirs=../locale -D language={}"'.format(
            translation.lang)
    logging.info("Running make %s", maketarget)
    logname = os.path.basename(checkout) + ".log"
    shell_out("cd Doc; make %s SPHINXBUILD=%s %s >> %s 2>&1" %
              (sphinxopts, sphinxbuild, maketarget,
               os.path.join(log_directory, logname)))
    changed = changed_files(os.path.join(checkout, "Doc/build/html"), target)
    logging.info("Copying HTML files to %s", target)
    os.makedirs(target, mode=0o775, exist_ok=True)
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


def copy_po_files(translation, version, build_root):
    locale_dir = os.path.join(build_root,
                              "python" + str(version).replace('.', ''),
                              'locale',
                              translation.lang,
                              'LC_MESSAGES')
    os.makedirs(locale_dir, mode=0o775, exist_ok=True)
    shell_out("rsync -a --include '*.po' --include '*/' --exclude '*' {}/ {}".format(
        os.path.join(build_root,
                     'i18n',
                     translation.repo.name,
                     translation.po_path),
        locale_dir))


def build_devguide(devguide_checkout, devguide_target, sphinxbuild,
                   skip_cache_invalidation=False):
    build_directory = os.path.join(devguide_checkout, "build/html")
    logging.info("Building devguide")
    shell_out("git -C %s pull" % (devguide_checkout,))
    shell_out("%s %s %s" % (sphinxbuild, devguide_checkout, build_directory))
    changed = changed_files(build_directory, devguide_target)
    shell_out("mkdir -p {}".format(devguide_target))
    shell_out("find %s -type d -exec chmod o+x {} ';'" % (build_directory,))
    shell_out("cp -a {}/* {}".format(build_directory, devguide_target))
    shell_out("chmod -R o+r %s" % (devguide_target,))
    if changed and not skip_cache_invalidation:
        prefix = os.path.basename(devguide_target)
        to_purge = [prefix]
        to_purge.extend(prefix + "/" + p for p in changed)
        logging.info("Running CDN purge")
        shell_out("curl -X PURGE \"https://docs.python.org/{%s}\"" % ",".join(to_purge))


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
    parser.add_argument(
        "--git",
        help="Use git instead of mercurial.",
        action="store_true")
    parser.add_argument(
        "--log-directory",
        help="Directory used to store logs.",
        default="/var/log/docsbuild/")
    return parser.parse_args()


def check_environment(build_root):
    venv_path = os.path.join(build_root, "environment")
    if not os.path.isdir(venv_path):
        logging.error("venv is missing in %s, salt should have built it.",
                      venv_path)
        exit(1)

if __name__ == '__main__':
    args = parse_args()
    if sys.stderr.isatty():
        logging.basicConfig(format="%(levelname)s:%(message)s",
                            stream=sys.stderr)
    else:
        logging.basicConfig(format="%(levelname)s:%(asctime)s:%(message)s",
                            filename=os.path.join(args.log_directory,
                                                  "docsbuild.log"))
    logging.root.setLevel(logging.DEBUG)
    check_environment(args.build_root)
    sphinxbuild = os.path.join(args.build_root, "environment/bin/sphinx-build")
    try:
        if args.branch:
            build_one(args.branch, args.branch, args.devel, args.quick,
                      sphinxbuild,
                      args.build_root, args.www_root, None,
                      args.skip_cache_invalidation,
                      args.group, args.git, args.log_directory)
        else:
            for version, branch, devel in BRANCHES:
                build_one(version, branch, devel, args.quick, sphinxbuild,
                          args.build_root, args.www_root, None,
                          args.skip_cache_invalidation, args.group, args.git,
                          args.log_directory)
                for translations in I18N_CONF:
                    translation = translations.get(branch, translations['default'])
                    clone_repo(translation.repo.url,
                               os.path.join(args.build_root,
                                            'i18n',
                                            translation.repo.name),
                               translation.branch, True)
                    copy_po_files(translation, version, args.build_root)
                    build_one(version, branch, devel, args.quick, sphinxbuild,
                              args.build_root, args.www_root, translation,
                              args.skip_cache_invalidation, args.group, args.git,
                              args.log_directory)

            build_devguide(args.devguide_checkout, args.devguide_target,
                           sphinxbuild, args.skip_cache_invalidation)
    except Exception:
        logging.exception("docs build raised exception")
