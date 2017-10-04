#!/usr/bin/env python3

"""Build the Python docs for various branches and various languages.

Usage:

  build_docs.py [-h] [-d] [-q] [-b 3.6] [-r BUILD_ROOT] [-w WWW_ROOT]
                [--devguide-checkout DEVGUIDE_CHECKOUT]
                [--devguide-target DEVGUIDE_TARGET]
                [--skip-cache-invalidation] [--group GROUP] [--git]
                [--log-directory LOG_DIRECTORY]
                [--languages [fr [fr ...]]]


Without any arguments builds docs for all branches configured in the
global BRANCHES value and all languages configured in LANGUAGES,
ignoring the -d flag as it's given in the BRANCHES configuration.

-q selects "quick build", which means to build only HTML.

-d allow the docs to be built even if the branch is in
development mode (i.e. version contains a, b or c).

Translations are fetched from github repositories according to PEP
545.  --languages allow select translations, use "--languages" to
build all translations (default) or "--languages en" to skip all
translations (as en is the untranslated version)..

This script was originally created and by Georg Brandl in March
2010.
Modified by Benjamin Peterson to do CDN cache invalidation.
Modified by Julien Palard to build translations.

"""

import logging
import os
import subprocess
import sys
import shutil


BRANCHES = [
    # version, isdev
    (3.6, False),
    (3.7, True),
    (2.7, False)
]

LANGUAGES = [
    'en',
    'fr',
    'ja'
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


def build_one(version, isdev, quick, venv, build_root, www_root,
              skip_cache_invalidation=False, group='docs',
              log_directory='/var/log/docsbuild/', language=None):
    if not language:
        language = 'en'
    checkout = build_root + "/python" + str(version).replace('.', '')
    logging.info("Build start for version: %s, language: %s",
                 str(version), language)
    sphinxopts = ''
    if language == 'en':
        target = os.path.join(www_root, str(version))
    else:
        language_dir = os.path.join(www_root, language)
        os.makedirs(language_dir, exist_ok=True)
        os.chmod(language_dir, 0o775)
        target = os.path.join(language_dir, str(version))
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
    os.makedirs(target, exist_ok=True)
    try:
        os.chmod(target, 0o775)
        shell_out("chgrp -R {group} {file}".format(group=group, file=target))
    except (PermissionError, subprocess.CalledProcessError) as err:
        logging.warning("Can't change mod or group of %s: %s",
                        target, str(err))
    os.chdir(checkout)

    logging.info("Updating checkout")
    shell_out("git reset --hard HEAD")
    shell_out("git pull --ff-only")
    maketarget = "autobuild-" + ("dev" if isdev else "stable") + ("-html" if quick else "")
    logging.info("Running make %s", maketarget)
    logname = "{}-{}.log".format(os.path.basename(checkout), language)
    python = os.path.join(venv, "bin/python")
    sphinxbuild = os.path.join(venv, "bin/sphinx-build")
    blurb = os.path.join(venv, "bin/blurb")
    shell_out(
        "cd Doc; make PYTHON=%s SPHINXBUILD=%s BLURB=%s VENVDIR=%s SPHINXOPTS='%s' %s >> %s 2>&1" %
        (python, sphinxbuild, blurb, venv, sphinxopts, maketarget,
         os.path.join(log_directory, logname)))
    shell_out("chgrp -R {group} {file}".format(
        group=group, file=log_directory))
    changed = changed_files(os.path.join(checkout, "Doc/build/html"), target)
    logging.info("Copying HTML files to %s", target)
    shell_out("chown -R :{} Doc/build/html/".format(group))
    shell_out("chmod -R o+r Doc/build/html/")
    shell_out("find Doc/build/html/ -type d -exec chmod o+x {} ';'")
    shell_out("rsync -a {delete} Doc/build/html/ {target}".format(
        delete="" if quick else "--delete-delay",
        target=target))
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
        targets_dir = os.path.dirname(target)
        prefixes = shell_out('find -L {} -samefile {}'.format(
            targets_dir, target)).replace(targets_dir + '/', '')
        prefixes = [prefix + '/' for prefix in prefixes.split('\n') if prefix]
        to_purge = prefixes[:]
        for prefix in prefixes:
            to_purge.extend(prefix + p for p in changed)
        logging.info("Running CDN purge")
        shell_out("curl -X PURGE \"https://docs.python.org/{%s}\"" % ",".join(to_purge))

    logging.info("Finished %s", checkout)


def build_devguide(devguide_checkout, devguide_target, venv,
                   skip_cache_invalidation=False):
    build_directory = os.path.join(devguide_checkout, "build/html")
    logging.info("Building devguide")
    shell_out("git -C %s pull" % (devguide_checkout,))
    sphinxbuild = os.path.join(venv, "bin/sphinx-build")
    shell_out("%s %s %s" % (sphinxbuild, devguide_checkout, build_directory))
    changed = changed_files(build_directory, devguide_target)
    shell_out("mkdir -p {}".format(devguide_target))
    shell_out("find %s -type d -exec chmod o+x {} ';'" % (build_directory,))
    shell_out("rsync -a --delete-delay {}/ {}".format(
        build_directory, devguide_target))
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
        default=True,
        help="Deprecated: Use git instead of mercurial. "
        "Defaults to True for compatibility.",
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
    venv = os.path.join(args.build_root, "venv")
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
                build_one(version, devel, args.quick, venv,
                          args.build_root, args.www_root,
                          args.skip_cache_invalidation, args.group,
                          args.log_directory, language)
            except Exception:
                logging.exception("docs build raised exception")
    build_devguide(args.devguide_checkout, args.devguide_target,
                   venv, args.skip_cache_invalidation)


if __name__ == '__main__':
    main()
