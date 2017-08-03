#!/usr/bin/env python3

# Runs a build of the Python docs for various branches.
#
# Usage:
#
#   build_docs.py [-h] [-d] [-q] [-b 3.6] [-r BUILD_ROOT] [-w WWW_ROOT]
#                 [--devguide-checkout DEVGUIDE_CHECKOUT]
#                 [--devguide-target DEVGUIDE_TARGET]
#                 [--skip-cache-invalidation] [--group GROUP] [--git]
#                 [--log-directory LOG_DIRECTORY]
#                 [--languages [fr [fr ...]]]
#
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
# Translations are fetched from github repositories according to PEP
# 545.  --languages allow select translations, use "--languages" to
# build all translations (default) or "--languages en" to skip all
# translations (as en is the untranslated version)..
#
# This script was originally created and by Georg Brandl in March 2010. Modified
# by Benjamin Peterson to do CDN cache invalidation.

import getopt
import logging
import os
import subprocess
import sys
import shutil


BRANCHES = [
    # version, isdev
    (3.5, False),
    (3.6, False),
    (3.7, True),
    (2.7, False)
]

LANGUAGES = [
    'en',
    'fr'
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
        return subprocess.check_output(cmd, shell=True,
                                       stdin=subprocess.PIPE,
                                       stderr=subprocess.STDOUT,
                                       universal_newlines=True)
    except subprocess.CalledProcessError as e:
        logging.debug("Command failed with output %r", e.output)
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


def git_clone(repository, directory, branch=None):
    """Clone or update the given repository in the given directory.
    Optionally checking out a branch.
    """
    logging.info("Updating repository %s in %s", repository, directory)
    try:
        if branch:
            shell_out("git -C {} checkout {}".format(directory, branch))
        shell_out("git -C {} pull --ff-only".format(directory))
    except subprocess.CalledProcessError:
        if os.path.exists(directory):
            shutil.rmtree(directory)
        logging.info("Cloning %s into %s", repository, repository)
        os.makedirs(directory, mode=0o775)
        shell_out("git clone --depth 1 --no-single-branch {} {}".format(
            repository, directory))
        if branch:
            shell_out("git -C {} checkout {}".format(directory, branch))


def pep_545_tag_to_gettext_tag(tag):
    """Transforms PEP 545 language tags like "pt-br" to gettext language
    tags like "pt_BR". (Note that none of those are IETF language tags
    like "pt-BR").
    """
    if '-' not in tag:
        return tag
    language, region = tag.split('-')
    return language + '_' + region.upper()


def translation_branch(locale_repo, locale_clone_dir, needed_version):
    """Some cpython versions may be untranslated, being either too old or
    too new.

    This function looks for remote branches on the given repo, and
    returns the name of the nearest existing branch.
    """
    git_clone(locale_repo, locale_clone_dir)
    remote_branches = shell_out(
        "git -C {} branch -r".format(locale_clone_dir))
    translated_branches = []
    for translated_branch in remote_branches.split('\n'):
        if not translated_branch:
            continue
        try:
            translated_branches.append(float(translated_branch.split('/')[1]))
        except ValueError:
            pass  # Skip non-version branches like 'master' if they exists.
    return sorted(translated_branches, key=lambda x: abs(needed_version - x))[0]


def build_one(version, isdev, quick, sphinxbuild, build_root, www_root,
              skip_cache_invalidation=False, group='docs', git=False,
              log_directory='/var/log/docsbuild/', language='en'):
    checkout = build_root + "/python" + str(version).replace('.', '')
    sphinxopts = ''
    if not language or language == 'en':
        target = os.path.join(www_root, str(version))
    else:
        target = os.path.join(www_root, language, str(version))
        gettext_language_tag = pep_545_tag_to_gettext_tag(language)
        locale_dirs = os.path.join(build_root, 'locale')
        locale_clone_dir = os.path.join(
            locale_dirs, gettext_language_tag, 'LC_MESSAGES')
        locale_repo = 'https://github.com/python/python-docs-{}.git'.format(
            language)
        git_clone(locale_repo, locale_clone_dir,
                  translation_branch(locale_repo, locale_clone_dir,
                                     version))
        sphinxopts += ('-D locale_dirs={} '
                       '-D language={} '
                       '-D gettext_compact=0').format(locale_dirs,
                                                      gettext_language_tag)
    if not os.path.exists(target):
        os.makedirs(target, mode=0o775)
    shell_out("chgrp -R {group} {file}".format(group=group, file=target))
    logging.info("Doc autobuild started in %s", checkout)
    os.chdir(checkout)

    logging.info("Updating checkout")
    if git:
        shell_out("git reset --hard HEAD")
        shell_out("git pull --ff-only")
    else:
        shell_out("hg pull -u")

    maketarget = "autobuild-" + ("dev" if isdev else "stable") + ("-html" if quick else "")
    logging.info("Running make %s", maketarget)
    logname = os.path.basename(checkout) + ".log"
    shell_out("cd Doc; make SPHINXBUILD=%s SPHINXOPTS='%s' %s >> %s 2>&1" %
              (sphinxbuild, sphinxopts, maketarget,
               os.path.join(log_directory, logname)))

    changed = changed_files(os.path.join(checkout, "Doc/build/html"), target)
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
    parser.add_argument(
        "--languages",
        nargs='*',
        default=LANGUAGES,
        help="Language translation, as a PEP 545 language tag like"
        " 'fr' or 'pt-br'.",
        metavar='fr')
    return parser.parse_args()


def main():
    args = parse_args()
    if sys.stderr.isatty():
        logging.basicConfig(format="%(levelname)s:%(message)s",
                            stream=sys.stderr)
    else:
        logging.basicConfig(format="%(levelname)s:%(asctime)s:%(message)s",
                            filename=os.path.join(args.log_directory,
                                                  "docsbuild.log"))
    logging.root.setLevel(logging.DEBUG)
    sphinxbuild = os.path.join(args.build_root, "environment/bin/sphinx-build")
    if args.branch:
        branches_to_do = [(args.branch, args.devel)]
    else:
        branches_to_do = BRANCHES
    if not args.languages:
        # Allow "--languages" to build all languages (as if not given)
        # instead of none.  "--languages en" builds *no* translation,
        # as "en" is the untranslated one.
        args.languages = LANGUAGES
    for version, devel in branches_to_do:
        for language in args.languages:
            try:
                build_one(version, devel, args.quick, sphinxbuild,
                          args.build_root, args.www_root,
                          args.skip_cache_invalidation, args.group, args.git,
                          args.log_directory, language)
            except Exception:
                logging.exception("docs build raised exception")
    build_devguide(args.devguide_checkout, args.devguide_target,
                   sphinxbuild, args.skip_cache_invalidation)


if __name__ == '__main__':
    main()
