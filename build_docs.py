#!/usr/bin/env python3

"""Build the Python docs for various branches and various languages.

Usage:

  build_docs.py [-h] [-d] [-q] [-b 3.7] [-r BUILD_ROOT] [-w WWW_ROOT]
                [--skip-cache-invalidation] [--group GROUP] [--git]
                [--log-directory LOG_DIRECTORY]
                [--languages [fr [fr ...]]]


Without any arguments builds docs for all active versions configured in the
global VERSIONS list and all languages configured in the LANGUAGES list,
ignoring the -d flag as it's given in the VERSIONS configuration.

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

from bisect import bisect_left as bisect
from collections import namedtuple, OrderedDict
from contextlib import contextmanager, suppress
import filecmp
import json
import logging
import logging.handlers
import os
from pathlib import Path
import re
from shlex import quote
import shutil
from string import Template
import subprocess
import sys
from datetime import datetime

HERE = Path(__file__).resolve().parent

try:
    import sentry_sdk
except ImportError:
    sentry_sdk = None
else:
    sentry_sdk.init()

VERSION = "19.0"


class Version:
    STATUSES = {"EOL", "security-fixes", "stable", "pre-release", "in development"}

    def __init__(self, name, branch, status):
        if status not in self.STATUSES:
            raise ValueError(
                "Version status expected to be in {}".format(", ".join(self.STATUSES))
            )
        self.name = name
        self.branch = branch
        self.status = status

    @property
    def url(self):
        return "https://docs.python.org/{}/".format(self.name)

    @property
    def title(self):
        return "Python {} ({})".format(self.name, self.status)


Language = namedtuple(
    "Language", ["tag", "iso639_tag", "name", "in_prod", "sphinxopts"]
)

# EOL and security-fixes are not automatically built, no need to remove them
# from the list.
VERSIONS = [
    Version("2.7", "2.7", "EOL"),
    Version("3.5", "3.5", "security-fixes"),
    Version("3.6", "3.6", "security-fixes"),
    Version("3.7", "3.7", "stable"),
    Version("3.8", "3.8", "stable"),
    Version("3.9", "3.9", "pre-release"),
    Version("3.10", "master", "in development"),
]

XELATEX_DEFAULT = (
    "-D latex_engine=xelatex",
    "-D latex_elements.inputenc=",
    "-D latex_elements.fontenc=",
)

PLATEX_DEFAULT = (
    "-D latex_engine=platex",
    "-D latex_elements.inputenc=",
    "-D latex_elements.fontenc=",
)

XELATEX_WITH_FONTSPEC = (
    "-D latex_engine=xelatex",
    "-D latex_elements.inputenc=",
    r"-D latex_elements.fontenc=\\usepackage{fontspec}",
)

XELATEX_FOR_KOREAN = (
    "-D latex_engine=xelatex",
    "-D latex_elements.inputenc=",
    "-D latex_elements.fontenc=",
    r"-D latex_elements.preamble=\\usepackage{kotex}\\setmainhangulfont{UnBatang}\\setsanshangulfont{UnDotum}\\setmonohangulfont{UnTaza}",
)

XELATEX_WITH_CJK = (
    "-D latex_engine=xelatex",
    "-D latex_elements.inputenc=",
    r"-D latex_elements.fontenc=\\usepackage{xeCJK}",
)

LANGUAGES = {
    Language("en", "en", "English", True, XELATEX_DEFAULT),
    Language("es", "es", "Spanish", False, XELATEX_WITH_FONTSPEC),
    Language("fr", "fr", "French", True, XELATEX_WITH_FONTSPEC),
    Language("id", "id", "Indonesian", False, XELATEX_DEFAULT),
    Language("ja", "ja", "Japanese", True, PLATEX_DEFAULT),
    Language("ko", "ko", "Korean", True, XELATEX_FOR_KOREAN),
    Language("pt-br", "pt_BR", "Brazilian Portuguese", True, XELATEX_DEFAULT),
    Language("zh-cn", "zh_CN", "Simplified Chinese", True, XELATEX_WITH_CJK),
    Language("zh-tw", "zh_TW", "Traditional Chinese", True, XELATEX_WITH_CJK),
}

DEFAULT_LANGUAGES_SET = {language.tag for language in LANGUAGES if language.in_prod}


def shell_out(cmd, shell=False, logfile=None):
    logging.debug("Running command %r", cmd)
    now = str(datetime.now())
    try:
        output = subprocess.check_output(
            cmd,
            shell=shell,
            stdin=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="backslashreplace",
        )
        if logfile:
            with open(logfile, "a+") as log:
                log.write("# " + now + "\n")
                log.write("# Command {cmd!r} ran successfully:".format(cmd=cmd))
                log.write(output)
                log.write("\n\n")
        return output
    except subprocess.CalledProcessError as e:
        if sentry_sdk:
            with sentry_sdk.push_scope() as scope:
                scope.fingerprint = ["{{ default }}", str(cmd)]
                sentry_sdk.capture_exception(e)
        if logfile:
            with open(logfile, "a+") as log:
                log.write("# " + now + "\n")
                log.write("# Command {cmd!r} failed:".format(cmd=cmd))
                log.write(e.output)
                log.write("\n\n")
            logging.error("Command failed (see %s at %s)", logfile, now)
        else:
            logging.error("Command failed with output %r", e.output)


def changed_files(left, right):
    """Compute a list of different files between left and right, recursively.
    Resulting paths are relative to left.
    """
    changed = []

    def traverse(dircmp_result):
        base = Path(dircmp_result.left).relative_to(left)
        changed.extend(str(base / file) for file in dircmp_result.diff_files)
        for dircomp in dircmp_result.subdirs.values():
            traverse(dircomp)

    traverse(filecmp.dircmp(left, right))
    return changed


def git_clone(repository, directory, branch=None):
    """Clone or update the given repository in the given directory.
    Optionally checking out a branch.
    """
    logging.info("Updating repository %s in %s", repository, directory)
    try:
        if not os.path.isdir(os.path.join(directory, ".git")):
            raise AssertionError("Not a git repository.")
        shell_out(["git", "-C", directory, "fetch"])
        if branch:
            shell_out(["git", "-C", directory, "checkout", branch])
            shell_out(["git", "-C", directory, "reset", "--hard", "origin/" + branch])
    except (subprocess.CalledProcessError, AssertionError):
        if os.path.exists(directory):
            shutil.rmtree(directory)
        logging.info("Cloning %s into %s", repository, directory)
        os.makedirs(directory, mode=0o775)
        shell_out(
            ["git", "clone", "--depth=1", "--no-single-branch", repository, directory]
        )
        if branch:
            shell_out(["git", "-C", directory, "checkout", branch])


def version_to_tuple(version):
    return tuple(int(part) for part in version.split("."))


def tuple_to_version(version_tuple):
    return ".".join(str(part) for part in version_tuple)


def locate_nearest_version(available_versions, target_version):
    """Look for the nearest version of target_version in available_versions.
    Versions are to be given as tuples, like (3, 7) for 3.7.

    >>> locate_nearest_version(["2.7", "3.6", "3.7", "3.8"], "3.9")
    '3.8'
    >>> locate_nearest_version(["2.7", "3.6", "3.7", "3.8"], "3.5")
    '3.6'
    >>> locate_nearest_version(["2.7", "3.6", "3.7", "3.8"], "2.6")
    '2.7'
    >>> locate_nearest_version(["2.7", "3.6", "3.7", "3.8"], "3.10")
    '3.8'
    >>> locate_nearest_version(["2.7", "3.6", "3.7", "3.8"], "3.7")
    '3.7'
    """

    available_versions_tuples = sorted(
        [
            version_to_tuple(available_version)
            for available_version in set(available_versions)
        ]
    )
    target_version_tuple = version_to_tuple(target_version)
    try:
        found = available_versions_tuples[
            bisect(available_versions_tuples, target_version_tuple)
        ]
    except IndexError:
        found = available_versions_tuples[-1]
    return tuple_to_version(found)


def translation_branch(locale_repo, locale_clone_dir, needed_version):
    """Some cpython versions may be untranslated, being either too old or
    too new.

    This function looks for remote branches on the given repo, and
    returns the name of the nearest existing branch.
    """
    git_clone(locale_repo, locale_clone_dir)
    remote_branches = shell_out(["git", "-C", locale_clone_dir, "branch", "-r"])
    branches = []
    for branch in remote_branches.split("\n"):
        if re.match(r".*/[0-9]+\.[0-9]+$", branch):
            branches.append(branch.split("/")[-1])
    return locate_nearest_version(branches, needed_version)


@contextmanager
def edit(file):
    """Context manager to edit a file "in place", use it as:
    with edit("/etc/hosts") as i, o:
        for line in i:
            o.write(line.replace("localhoat", "localhost"))
    """
    temporary = file.with_name(file.name + ".tmp")
    with suppress(OSError):
        os.unlink(temporary)
    with open(file) as input_file:
        with open(temporary, "w") as output_file:
            yield input_file, output_file
    os.rename(temporary, file)


def picker_label(version):
    if version.status == "in development":
        return "dev ({})".format(version.name)
    if version.status == "pre-release":
        return "pre ({})".format(version.name)
    return version.name


def setup_indexsidebar(dest_path):
    versions_li = []
    for version in sorted(
        VERSIONS, key=lambda v: version_to_tuple(v.name), reverse=True,
    ):
        versions_li.append(
            '<li><a href="{}">{}</a></li>'.format(version.url, version.title)
        )

    with open(HERE / "templates" / "indexsidebar.html") as sidebar_template_file:
        with open(dest_path, "w") as sidebar_file:
            template = Template(sidebar_template_file.read())
            sidebar_file.write(
                template.safe_substitute({"VERSIONS": "\n".join(versions_li)})
            )


def setup_switchers(html_root):
    """Setup cross-links between cpython versions:
    - Cross-link various languages in a language switcher
    - Cross-link various versions in a version switcher
    """
    with open(HERE / "templates" / "switchers.js") as switchers_template_file:
        with open(
            os.path.join(html_root, "_static", "switchers.js"), "w"
        ) as switchers_file:
            template = Template(switchers_template_file.read())
            switchers_file.write(
                template.safe_substitute(
                    {
                        "LANGUAGES": json.dumps(
                            OrderedDict(
                                sorted(
                                    [
                                        (language.tag, language.name)
                                        for language in LANGUAGES
                                        if language.in_prod
                                    ]
                                )
                            )
                        ),
                        "VERSIONS": json.dumps(
                            OrderedDict(
                                [
                                    (version.name, picker_label(version))
                                    for version in sorted(
                                        VERSIONS,
                                        key=lambda v: version_to_tuple(v.name),
                                        reverse=True,
                                    )
                                ]
                            )
                        ),
                    }
                )
            )
    for file in Path(html_root).glob("**/*.html"):
        depth = len(file.relative_to(html_root).parts) - 1
        script = """    <script type="text/javascript" src="{}_static/switchers.js"></script>\n""".format(
            "../" * depth
        )
        with edit(file) as (i, o):
            for line in i:
                if line == script:
                    continue
                if line == "  </body>\n":
                    o.write(script)
                o.write(line)


def build_one(
    version, quick, venv, build_root, group, log_directory, language: Language,
):
    checkout = os.path.join(
        build_root, version.name, "cpython-{lang}".format(lang=language.tag)
    )
    logging.info(
        "Build start for version: %s, language: %s", version.name, language.tag
    )
    sphinxopts = list(language.sphinxopts)
    sphinxopts.extend(["-q"])
    if language.tag != "en":
        locale_dirs = os.path.join(build_root, version.name, "locale")
        locale_clone_dir = os.path.join(locale_dirs, language.iso639_tag, "LC_MESSAGES")
        locale_repo = "https://github.com/python/python-docs-{}.git".format(
            language.tag
        )
        git_clone(
            locale_repo,
            locale_clone_dir,
            translation_branch(locale_repo, locale_clone_dir, version.name),
        )
        sphinxopts.extend(
            (
                "-D locale_dirs={}".format(locale_dirs),
                "-D language={}".format(language.iso639_tag),
                "-D gettext_compact=0",
            )
        )
    git_clone("https://github.com/python/cpython.git", checkout, version.branch)
    maketarget = (
        "autobuild-"
        + ("dev" if version.status == "in development" else "stable")
        + ("-html" if quick else "")
    )
    logging.info("Running make %s", maketarget)
    logname = "cpython-{lang}-{version}.log".format(
        lang=language.tag, version=version.name
    )
    python = os.path.join(venv, "bin/python")
    sphinxbuild = os.path.join(venv, "bin/sphinx-build")
    blurb = os.path.join(venv, "bin/blurb")
    # Disable cpython switchers, we handle them now:
    shell_out(
        [
            "sed",
            "-i",
            "s/ *-A switchers=1//",
            os.path.join(checkout, "Doc", "Makefile"),
        ]
    )
    setup_indexsidebar(
        os.path.join(checkout, "Doc", "tools", "templates", "indexsidebar.html")
    )
    shell_out(
        [
            "make",
            "-C",
            os.path.join(checkout, "Doc"),
            "PYTHON=" + python,
            "SPHINXBUILD=" + sphinxbuild,
            "BLURB=" + blurb,
            "VENVDIR=" + venv,
            "SPHINXOPTS=" + " ".join(sphinxopts),
            "SPHINXERRORHANDLING=",
            maketarget,
        ],
        logfile=os.path.join(log_directory, logname),
    )
    shell_out(["chgrp", "-R", group, log_directory])
    setup_switchers(os.path.join(checkout, "Doc", "build", "html"))
    logging.info("Build done for version: %s, language: %s", version.name, language.tag)


def copy_build_to_webroot(
    build_root,
    version,
    language: Language,
    group,
    quick,
    skip_cache_invalidation,
    www_root,
):
    """Copy a given build to the appropriate webroot with appropriate rights.
    """
    logging.info(
        "Publishing start for version: %s, language: %s", version.name, language.tag
    )
    checkout = os.path.join(
        build_root, version.name, "cpython-{lang}".format(lang=language.tag)
    )
    if language.tag == "en":
        target = os.path.join(www_root, version.name)
    else:
        language_dir = os.path.join(www_root, language.tag)
        os.makedirs(language_dir, exist_ok=True)
        try:
            shell_out(["chgrp", "-R", group, language_dir])
        except subprocess.CalledProcessError as err:
            logging.warning("Can't change group of %s: %s", language_dir, str(err))
        os.chmod(language_dir, 0o775)
        target = os.path.join(language_dir, version.name)

    os.makedirs(target, exist_ok=True)
    try:
        os.chmod(target, 0o775)
    except PermissionError as err:
        logging.warning("Can't change mod of %s: %s", target, str(err))
    try:
        shell_out(["chgrp", "-R", group, target])
    except subprocess.CalledProcessError as err:
        logging.warning("Can't change group of %s: %s", target, str(err))

    changed = changed_files(os.path.join(checkout, "Doc/build/html"), target)
    logging.info("Copying HTML files to %s", target)
    shell_out(["chown", "-R", ":" + group, os.path.join(checkout, "Doc/build/html/")])
    shell_out(["chmod", "-R", "o+r", os.path.join(checkout, "Doc/build/html/")])
    shell_out(
        [
            "find",
            os.path.join(checkout, "Doc/build/html/"),
            "-type",
            "d",
            "-exec",
            "chmod",
            "o+x",
            "{}",
            ";",
        ]
    )
    if quick:
        shell_out(["rsync", "-a", os.path.join(checkout, "Doc/build/html/"), target])
    else:
        shell_out(
            [
                "rsync",
                "-a",
                "--delete-delay",
                "--filter",
                "P archives/",
                os.path.join(checkout, "Doc/build/html/"),
                target,
            ]
        )
    if not quick:
        logging.debug("Copying dist files")
        shell_out(["chown", "-R", ":" + group, os.path.join(checkout, "Doc/dist/")])
        shell_out(
            ["chmod", "-R", "o+r", os.path.join(checkout, os.path.join("Doc/dist/"))]
        )
        shell_out(["mkdir", "-m", "o+rx", "-p", os.path.join(target, "archives")])
        shell_out(["chown", ":" + group, os.path.join(target, "archives")])
        shell_out(
            "cp -a {src} {dst}".format(
                src=os.path.join(checkout, "Doc/dist/*"),
                dst=os.path.join(target, "archives"),
            ),
            shell=True,
        )
        changed.append("archives/")
        for fn in os.listdir(os.path.join(target, "archives")):
            changed.append("archives/" + fn)

    logging.info("%s files changed", len(changed))
    if changed and not skip_cache_invalidation:
        targets_dir = www_root
        prefixes = shell_out(["find", "-L", targets_dir, "-samefile", target])
        prefixes = prefixes.replace(targets_dir + "/", "")
        prefixes = [prefix + "/" for prefix in prefixes.split("\n") if prefix]
        to_purge = prefixes[:]
        for prefix in prefixes:
            to_purge.extend(prefix + p for p in changed)
        logging.info("Running CDN purge")
        shell_out(
            ["curl", "-XPURGE", "https://docs.python.org/{%s}" % ",".join(to_purge)]
        )
    logging.info(
        "Publishing done for version: %s, language: %s", version.name, language.tag
    )


def head(lines, n=10):
    return "\n".join(lines.split("\n")[:n])


def version_info():
    platex_version = head(
        subprocess.check_output(["platex", "--version"], universal_newlines=True), n=3
    )

    xelatex_version = head(
        subprocess.check_output(["xelatex", "--version"], universal_newlines=True), n=2
    )
    print(
        """build_docs: {VERSION}

# platex

{platex_version}


# xelatex

{xelatex_version}
    """.format(
            VERSION=VERSION,
            platex_version=platex_version,
            xelatex_version=xelatex_version,
        )
    )


def parse_args():
    from argparse import ArgumentParser

    parser = ArgumentParser(
        description="Runs a build of the Python docs for various branches."
    )
    parser.add_argument(
        "-d",
        "--devel",
        action="store_true",
        help="Use make autobuild-dev instead of autobuild-stable",
    )
    parser.add_argument(
        "-q",
        "--quick",
        action="store_true",
        help="Make HTML files only (Makefile rules suffixed with -html).",
    )
    parser.add_argument(
        "-b",
        "--branch",
        metavar="3.6",
        help="Version to build (defaults to all maintained branches).",
    )
    parser.add_argument(
        "-r",
        "--build-root",
        help="Path to a directory containing a checkout per branch.",
        default="/srv/docsbuild",
    )
    parser.add_argument(
        "-w",
        "--www-root",
        help="Path where generated files will be copied.",
        default="/srv/docs.python.org",
    )
    parser.add_argument(
        "--skip-cache-invalidation",
        help="Skip fastly cache invalidation.",
        action="store_true",
    )
    parser.add_argument(
        "--group",
        help="Group files on targets and www-root file should get.",
        default="docs",
    )
    parser.add_argument(
        "--git",
        default=True,
        help="Deprecated: Use git instead of mercurial. "
        "Defaults to True for compatibility.",
        action="store_true",
    )
    parser.add_argument(
        "--log-directory",
        help="Directory used to store logs.",
        default="/var/log/docsbuild/",
    )
    parser.add_argument(
        "--languages",
        nargs="*",
        default=DEFAULT_LANGUAGES_SET,
        help="Language translation, as a PEP 545 language tag like" " 'fr' or 'pt-br'.",
        metavar="fr",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Get build_docs and dependencies version info",
    )
    return parser.parse_args()


def setup_logging(log_directory):
    if sys.stderr.isatty():
        logging.basicConfig(format="%(levelname)s:%(message)s", stream=sys.stderr)
    else:
        handler = logging.handlers.WatchedFileHandler(
            os.path.join(log_directory, "docsbuild.log")
        )
        handler.setFormatter(logging.Formatter("%(levelname)s:%(asctime)s:%(message)s"))
        logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.DEBUG)


def main():
    args = parse_args()
    languages_dict = {language.tag: language for language in LANGUAGES}
    if args.version:
        version_info()
        exit(0)
    if args.log_directory:
        args.log_directory = os.path.abspath(args.log_directory)
    if args.build_root:
        args.build_root = os.path.abspath(args.build_root)
    if args.www_root:
        args.www_root = os.path.abspath(args.www_root)
    setup_logging(args.log_directory)
    venv = os.path.join(args.build_root, "venv")
    if args.branch:
        versions_to_build = [
            version
            for version in VERSIONS
            if version.name == args.branch or version.branch == args.branch
        ]
    else:
        versions_to_build = [
            version
            for version in VERSIONS
            if version.status != "EOL" and version.status != "security-fixes"
        ]
    if args.languages:
        languages = [languages_dict[tag] for tag in args.languages]
    else:
        # Allow "--languages" to build all languages (as if not given)
        # instead of none.  "--languages en" builds *no* translation,
        # as "en" is the untranslated one.
        languages = [
            language for language in LANGUAGES if language.tag in DEFAULT_LANGUAGES_SET
        ]
    for version in versions_to_build:
        for language in languages:
            if sentry_sdk:
                with sentry_sdk.configure_scope() as scope:
                    scope.set_tag("version", version.name)
                    scope.set_tag("language", language.tag)
            try:
                build_one(
                    version,
                    args.quick,
                    venv,
                    args.build_root,
                    args.group,
                    args.log_directory,
                    language,
                )
                copy_build_to_webroot(
                    args.build_root,
                    version,
                    language,
                    args.group,
                    args.quick,
                    args.skip_cache_invalidation,
                    args.www_root,
                )
            except Exception as err:
                logging.exception(
                    "Exception while building %s version %s",
                    language.tag,
                    version.name,
                )
                if sentry_sdk:
                    sentry_sdk.capture_exception(err)


if __name__ == "__main__":
    main()
