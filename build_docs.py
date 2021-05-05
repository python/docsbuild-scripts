#!/usr/bin/env python3

"""Build the Python docs for various branches and various languages.

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

import filecmp
import json
import logging
import logging.handlers
import os
import re
import shlex
import shutil
import subprocess
import sys
from bisect import bisect_left as bisect
from collections import OrderedDict, namedtuple
from contextlib import contextmanager, suppress
from pathlib import Path
from string import Template
from textwrap import indent

import jinja2

HERE = Path(__file__).resolve().parent

try:
    import sentry_sdk
except ImportError:
    sentry_sdk = None
else:
    sentry_sdk.init()

VERSION = "19.0"
DEFAULT_SPHINX_VERSION = "2.3.1"

if not hasattr(shlex, "join"):
    # Add shlex.join if missing (pre 3.8)
    shlex.join = lambda split_command: " ".join(
        shlex.quote(arg) for arg in split_command
    )


class Version:
    STATUSES = {"EOL", "security-fixes", "stable", "pre-release", "in development"}

    def __init__(
        self,
        name,
        branch,
        status,
        sphinx_version=DEFAULT_SPHINX_VERSION,
        sphinxopts=[],
    ):
        if status not in self.STATUSES:
            raise ValueError(
                "Version status expected to be in {}".format(", ".join(self.STATUSES))
            )
        self.name = name
        self.branch = branch
        self.status = status
        self.sphinx_version = sphinx_version
        self.sphinxopts = list(sphinxopts)

    @property
    def changefreq(self):
        return {"EOL": "never", "security-fixes": "yearly"}.get(self.status, "daily")

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
# from the list, this way we can still rebuild them manually as needed.
# Please pin the sphinx_versions of EOL and security-fixes, as we're not maintaining
# their doc, they don't follow Sphinx deprecations.
VERSIONS = [
    Version("2.7", "2.7", "EOL", sphinx_version="2.3.1"),
    Version("3.5", "3.5", "EOL", sphinx_version="1.8.4"),
    Version("3.6", "3.6", "security-fixes", sphinx_version="2.3.1"),
    Version("3.7", "3.7", "security-fixes", sphinx_version="2.3.1"),
    Version("3.8", "3.8", "security-fixes", sphinx_version="2.4.4"),
    Version("3.9", "3.9", "stable", sphinx_version="2.4.4"),
    Version(
        "3.10", "3.10", "pre-release", sphinx_version="3.2.1", sphinxopts=["-j4"]
    ),
    Version(
        "3.11", "main", "in development", sphinx_version="3.2.1", sphinxopts=["-j4"]
    ),
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
    Language("es", "es", "Spanish", True, XELATEX_WITH_FONTSPEC),
    Language("fr", "fr", "French", True, XELATEX_WITH_FONTSPEC),
    Language("id", "id", "Indonesian", False, XELATEX_DEFAULT),
    Language("ja", "ja", "Japanese", True, PLATEX_DEFAULT),
    Language("ko", "ko", "Korean", True, XELATEX_FOR_KOREAN),
    Language("pt-br", "pt_BR", "Brazilian Portuguese", True, XELATEX_DEFAULT),
    Language("zh-cn", "zh_CN", "Simplified Chinese", True, XELATEX_WITH_CJK),
    Language("zh-tw", "zh_TW", "Traditional Chinese", True, XELATEX_WITH_CJK),
    Language("pl", "pl", "Polish", False, XELATEX_DEFAULT),
}


def run(cmd) -> subprocess.CompletedProcess:
    """Like subprocess.run, with logging before and after the command execution."""
    cmdstring = shlex.join(cmd)
    logging.debug("Run: %r", cmdstring)
    result = subprocess.run(
        cmd,
        stdin=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdout=subprocess.PIPE,
        encoding="utf-8",
        errors="backslashreplace",
    )
    if result.returncode:
        # Log last 20 lines, those are likely the interesting ones.
        logging.error(
            "Run KO: %r:\n%s",
            cmdstring,
            indent("\n".join(result.stdout.split("\n")[-20:]), "    "),
        )
    else:
        logging.debug("Run OK: %r", cmdstring)
    result.check_returncode()
    return result


def changed_files(left, right):
    """Compute a list of different files between left and right, recursively.
    Resulting paths are relative to left.
    """
    changed = []

    def traverse(dircmp_result):
        base = Path(dircmp_result.left).relative_to(left)
        for file in dircmp_result.diff_files:
            changed.append(str(base / file))
            if file == "index.html":
                changed.append(str(base) + "/")
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
        run(["git", "-C", directory, "fetch"])
        if branch:
            run(["git", "-C", directory, "checkout", branch])
            run(["git", "-C", directory, "reset", "--hard", "origin/" + branch])
    except (subprocess.CalledProcessError, AssertionError):
        if os.path.exists(directory):
            shutil.rmtree(directory)
        logging.info("Cloning %s into %s", repository, directory)
        os.makedirs(directory, mode=0o775)
        run(["git", "clone", "--depth=1", "--no-single-branch", repository, directory])
        if branch:
            run(["git", "-C", directory, "checkout", branch])


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
    remote_branches = run(["git", "-C", locale_clone_dir, "branch", "-r"]).stdout
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
        VERSIONS,
        key=lambda v: version_to_tuple(v.name),
        reverse=True,
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
    version,
    quick,
    venv,
    build_root,
    group,
    log_directory,
    language: Language,
):
    checkout = os.path.join(
        build_root, version.name, "cpython-{lang}".format(lang=language.tag)
    )
    logging.info(
        "Build start for version: %s, language: %s", version.name, language.tag
    )
    sphinxopts = list(language.sphinxopts) + list(version.sphinxopts)
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
    if version.status == "EOL":
        sphinxopts.append("-D html_context.outdated=1")
    git_clone("https://github.com/python/cpython.git", checkout, version.branch)
    maketarget = (
        "autobuild-"
        + ("dev" if version.status in ("in development", "pre-release") else "stable")
        + ("-html" if quick else "")
    )
    logging.info("Running make %s", maketarget)
    python = os.path.join(venv, "bin/python")
    sphinxbuild = os.path.join(venv, "bin/sphinx-build")
    blurb = os.path.join(venv, "bin/blurb")
    # Disable cpython switchers, we handle them now:
    run(
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
    run(
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
        ]
    )
    run(["mkdir", "-p", log_directory])
    run(["chgrp", "-R", group, log_directory])
    setup_switchers(os.path.join(checkout, "Doc", "build", "html"))
    logging.info("Build done for version: %s, language: %s", version.name, language.tag)


def build_venv(build_root, version, theme):
    """Build a venv for the specific version.
    This is used to pin old Sphinx versions to old cpython branches.
    """
    requirements = [
        "blurb",
        "jieba",
        theme,
        "sphinx=={}".format(version.sphinx_version),
    ]
    venv_path = os.path.join(build_root, "venv-with-sphinx-" + version.sphinx_version)
    run(["python3", "-m", "venv", venv_path])
    run(
        [os.path.join(venv_path, "bin", "python"), "-m", "pip", "install"]
        + requirements
    )
    return venv_path


def build_robots_txt(www_root, group, skip_cache_invalidation):
    if not Path(www_root).exists():
        logging.info("Skipping robots.txt generation (www root does not even exists).")
        return
    robots_file = os.path.join(www_root, "robots.txt")
    with open(HERE / "templates" / "robots.txt") as robots_txt_template_file:
        with open(robots_file, "w") as robots_txt_file:
            template = jinja2.Template(robots_txt_template_file.read())
            robots_txt_file.write(
                template.render(languages=LANGUAGES, versions=VERSIONS) + "\n"
            )
    os.chmod(robots_file, 0o775)
    run(["chgrp", group, robots_file])
    if not skip_cache_invalidation:
        run(
            [
                "curl",
                "--silent",
                "-XPURGE",
                "https://docs.python.org/robots.txt",
            ]
        )


def build_sitemap(www_root):
    if not Path(www_root).exists():
        logging.info("Skipping sitemap generation (www root does not even exists).")
        return
    with open(HERE / "templates" / "sitemap.xml") as sitemap_template_file:
        with open(os.path.join(www_root, "sitemap.xml"), "w") as sitemap_file:
            template = jinja2.Template(sitemap_template_file.read())
            sitemap_file.write(
                template.render(languages=LANGUAGES, versions=VERSIONS) + "\n"
            )


def copy_build_to_webroot(
    build_root,
    version,
    language: Language,
    group,
    quick,
    skip_cache_invalidation,
    www_root,
):
    """Copy a given build to the appropriate webroot with appropriate rights."""
    logging.info(
        "Publishing start for version: %s, language: %s", version.name, language.tag
    )
    Path(www_root).mkdir(parents=True, exist_ok=True)
    checkout = os.path.join(
        build_root, version.name, "cpython-{lang}".format(lang=language.tag)
    )
    if language.tag == "en":
        target = os.path.join(www_root, version.name)
    else:
        language_dir = os.path.join(www_root, language.tag)
        os.makedirs(language_dir, exist_ok=True)
        try:
            run(["chgrp", "-R", group, language_dir])
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
        run(["chgrp", "-R", group, target])
    except subprocess.CalledProcessError as err:
        logging.warning("Can't change group of %s: %s", target, str(err))

    changed = changed_files(os.path.join(checkout, "Doc/build/html"), target)
    logging.info("Copying HTML files to %s", target)
    run(["chown", "-R", ":" + group, os.path.join(checkout, "Doc/build/html/")])
    run(["chmod", "-R", "o+r", os.path.join(checkout, "Doc/build/html/")])
    run(
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
        run(["rsync", "-a", os.path.join(checkout, "Doc/build/html/"), target])
    else:
        run(
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
        run(["chown", "-R", ":" + group, os.path.join(checkout, "Doc/dist/")])
        run(["chmod", "-R", "o+r", os.path.join(checkout, os.path.join("Doc/dist/"))])
        run(["mkdir", "-m", "o+rx", "-p", os.path.join(target, "archives")])
        run(["chown", ":" + group, os.path.join(target, "archives")])
        run(
            [
                "cp",
                "-a",
                *[str(dist) for dist in (Path(checkout) / "Doc" / "dist").glob("*")],
                os.path.join(target, "archives"),
            ]
        )
        changed.append("archives/")
        for fn in os.listdir(os.path.join(target, "archives")):
            changed.append("archives/" + fn)

    logging.info("%s files changed", len(changed))
    if changed and not skip_cache_invalidation:
        targets_dir = www_root
        prefixes = run(["find", "-L", targets_dir, "-samefile", target]).stdout
        prefixes = prefixes.replace(targets_dir + "/", "")
        prefixes = [prefix + "/" for prefix in prefixes.split("\n") if prefix]
        to_purge = prefixes[:]
        for prefix in prefixes:
            to_purge.extend(prefix + p for p in changed)
        logging.info("Running CDN purge")
        run(["curl", "-XPURGE", "https://docs.python.org/{%s}" % ",".join(to_purge)])
    logging.info(
        "Publishing done for version: %s, language: %s", version.name, language.tag
    )


def head(lines, n=10):
    return "\n".join(lines.split("\n")[:n])


def version_info():
    try:
        platex_version = head(
            subprocess.check_output(["platex", "--version"], universal_newlines=True),
            n=3,
        )
    except FileNotFoundError:
        platex_version = "Not installed."

    try:
        xelatex_version = head(
            subprocess.check_output(["xelatex", "--version"], universal_newlines=True),
            n=2,
        )
    except FileNotFoundError:
        xelatex_version = "Not installed."
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
        default={language.tag for language in LANGUAGES},
        help="Language translation, as a PEP 545 language tag like" " 'fr' or 'pt-br'.",
        metavar="fr",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Get build_docs and dependencies version info",
    )
    parser.add_argument(
        "--theme",
        default="python-docs-theme",
        help="Python package to use for python-docs-theme: Usefull to test branches:"
        " --theme git+https://github.com/obulat/python-docs-theme@master",
    )
    return parser.parse_args()


def setup_logging(log_directory):
    if sys.stderr.isatty():
        logging.basicConfig(format="%(levelname)s:%(message)s", stream=sys.stderr)
    else:
        Path(log_directory).mkdir(parents=True, exist_ok=True)
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
    for version in versions_to_build:
        for language_tag in args.languages:
            if sentry_sdk:
                with sentry_sdk.configure_scope() as scope:
                    scope.set_tag("version", version.name)
                    scope.set_tag("language", language_tag)
            language = languages_dict[language_tag]
            try:
                venv = build_venv(args.build_root, version, args.theme)
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
                    language_tag,
                    version.name,
                )
                if sentry_sdk:
                    sentry_sdk.capture_exception(err)
    build_sitemap(args.www_root)
    build_robots_txt(args.www_root, args.group, args.skip_cache_invalidation)


if __name__ == "__main__":
    main()
