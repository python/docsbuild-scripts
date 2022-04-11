#!/usr/bin/env python3

"""Build the Python docs for various branches and various languages.

Without any arguments builds docs for all active versions configured in the
global VERSIONS list and all languages configured in the LANGUAGES list.

-q selects "quick build", which means to build only HTML.

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
from itertools import product
import json
import logging
import logging.handlers
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from bisect import bisect_left as bisect
from collections import OrderedDict, namedtuple
from contextlib import contextmanager, suppress
from pathlib import Path
from string import Template
from textwrap import indent

import zc.lockfile
import jinja2

HERE = Path(__file__).resolve().parent

try:
    import sentry_sdk
except ImportError:
    sentry_sdk = None
else:
    sentry_sdk.init()

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
        *,
        branch=None,
        tag=None,
        status,
        sphinx_version,
        sphinxopts=(),
    ):
        if status not in self.STATUSES:
            raise ValueError(
                "Version status expected to be in {}".format(", ".join(self.STATUSES))
            )
        self.name = name
        if branch is not None and tag is not None:
            raise ValueError("Please build a version from either a branch or a tag.")
        if branch is None and tag is None:
            raise ValueError("Please build a version with at least a branch or a tag.")
        self.branch_or_tag = branch or tag
        self.status = status
        self.sphinx_version = sphinx_version
        self.sphinxopts = list(sphinxopts)

    def __repr__(self):
        return f"Version({self.name})"

    @property
    def requirements(self):
        reqs = [
            "blurb",
            "jieba",
            "sphinx=={}".format(self.sphinx_version),
        ]
        if tuple(int(part) for part in self.sphinx_version.split(".")) < (4, 5):
            reqs += ["jinja2<3.1"]
        return reqs

    @property
    def changefreq(self):
        return {"EOL": "never", "security-fixes": "yearly"}.get(self.status, "daily")

    def as_tuple(self):
        return tuple(int(part) for part in self.name.split("."))

    @property
    def url(self):
        return "https://docs.python.org/{}/".format(self.name)

    @property
    def title(self):
        return "Python {} ({})".format(self.name, self.status)

    @staticmethod
    def filter(versions, branch=None):
        """Filter the given versions.

        If *branch* is given, only *versions* matching *branch* are returned.

        Else all live version are returned (this mean no EOL and no
        security-fixes branches).
        """
        if branch:
            return [v for v in versions if branch in (v.name, v.branch_or_tag)]
        return [v for v in versions if v.status not in ("EOL", "security-fixes")]

    @staticmethod
    def current_stable(versions):
        return max([v for v in versions if v.status == "stable"], key=Version.as_tuple)

    @staticmethod
    def current_dev(versions):
        return max([v for v in versions], key=Version.as_tuple)


Language = namedtuple(
    "Language", ["tag", "iso639_tag", "name", "in_prod", "sphinxopts"]
)

# EOL and security-fixes are not automatically built, no need to remove them
# from the list, this way we can still rebuild them manually as needed.
#
# Please keep the list in reverse-order for ease of editing.
VERSIONS = [
    Version(
        "3.11",
        branch="origin/main",
        status="in development",
        sphinx_version="4.2.0",
        sphinxopts=["-j4"],
    ),
    Version(
        "3.10",
        branch="origin/3.10",
        status="stable",
        sphinx_version="3.2.1",
        sphinxopts=["-j4"],
    ),
    Version(
        "3.9",
        branch="origin/3.9",
        status="stable",
        sphinx_version="2.4.4",
    ),
    Version(
        "3.8",
        branch="origin/3.8",
        status="security-fixes",
        sphinx_version="2.4.4",
    ),
    Version(
        "3.7",
        branch="origin/3.7",
        status="security-fixes",
        sphinx_version="2.3.1",
    ),
    Version(
        "3.6",
        tag="3.6",
        status="EOL",
        sphinx_version="2.3.1",
    ),
    Version(
        "3.5",
        tag="3.5",
        status="EOL",
        sphinx_version="1.8.4",
    ),
    Version(
        "2.7",
        tag="2.7",
        status="EOL",
        sphinx_version="2.3.1",
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
    r"-D latex_elements.preamble=\\usepackage{kotex}\\setmainhangulfont"
    r"{UnBatang}\\setsanshangulfont{UnDotum}\\setmonohangulfont{UnTaza}",
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
        check=False,
    )
    if result.returncode:
        # Log last 20 lines, those are likely the interesting ones.
        logging.error(
            "Run: %r KO:\n%s",
            cmdstring,
            indent("\n".join(result.stdout.split("\n")[-20:]), "    "),
        )
    else:
        logging.debug("Run: %r OK", cmdstring)
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


def git_clone(repository, directory, branch_or_tag=None):
    """Clone or update the given repository in the given directory.
    Optionally checking out a branch.
    """
    logging.info("Updating repository %s in %s", repository, directory)
    try:
        if not os.path.isdir(os.path.join(directory, ".git")):
            raise AssertionError("Not a git repository.")
        run(["git", "-C", directory, "fetch"])
        if branch_or_tag:
            run(["git", "-C", directory, "reset", "--hard", branch_or_tag, "--"])
            run(["git", "-C", directory, "clean", "-dfqx"])
    except (subprocess.CalledProcessError, AssertionError):
        if os.path.exists(directory):
            shutil.rmtree(directory)
        logging.info("Cloning %s into %s", repository, directory)
        os.makedirs(directory, mode=0o775)
        run(["git", "clone", repository, directory])
        if branch_or_tag:
            run(["git", "-C", directory, "reset", "--hard", branch_or_tag, "--"])


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


def translation_branch(locale_repo, locale_clone_dir, needed_version: str):
    """Some cpython versions may be untranslated, being either too old or
    too new.

    This function looks for remote branches on the given repo, and
    returns the name of the nearest existing branch.
    """
    git_clone(locale_repo, locale_clone_dir)
    remote_branches = run(["git", "-C", locale_clone_dir, "branch", "-r"]).stdout
    branches = re.findall(r"/([0-9]+\.[0-9]+)$", remote_branches, re.M)
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


def setup_indexsidebar(dest_path, current_version):
    with open(
        HERE / "templates" / "indexsidebar.html", encoding="UTF-8"
    ) as sidebar_template_file:
        sidebar_template = jinja2.Template(sidebar_template_file.read())
    with open(dest_path, "w", encoding="UTF-8") as sidebar_file:
        sidebar_file.write(
            sidebar_template.render(
                current_version=current_version,
                versions=sorted(
                    VERSIONS, key=lambda v: version_to_tuple(v.name), reverse=True
                ),
            )
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
        script = (
            '    <script type="text/javascript" src="{}_static/switchers.js">'.format(
                "../" * depth
            )
            + "</script>\n"
        )
        with edit(file) as (ifile, ofile):
            for line in ifile:
                if line == script:
                    continue
                if line == "  </body>\n":
                    ofile.write(script)
                ofile.write(line)


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
    args = parser.parse_args()
    if args.version:
        version_info()
        sys.exit(0)
    del args.version
    if args.log_directory:
        args.log_directory = os.path.abspath(args.log_directory)
    if args.build_root:
        args.build_root = os.path.abspath(args.build_root)
    if args.www_root:
        args.www_root = os.path.abspath(args.www_root)
    return args


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


class DocBuilder(
    namedtuple(
        "DocBuilder",
        "version, language, build_root, www_root, quick, group, "
        "log_directory, skip_cache_invalidation, theme",
    )
):
    def run(self):
        """Build and publish a Python doc, for a language, and a version."""
        try:
            self.build_venv()
            self.build()
            self.copy_build_to_webroot()
        except Exception as err:
            logging.exception(
                "Exception while building %s version %s",
                self.language.tag,
                self.version.name,
            )
            if sentry_sdk:
                sentry_sdk.capture_exception(err)

    @property
    def checkout(self):
        return os.path.join(self.build_root, "cpython")

    def build(self):
        logging.info(
            "Build start for version: %s, language: %s",
            self.version.name,
            self.language.tag,
        )
        sphinxopts = list(self.language.sphinxopts) + list(self.version.sphinxopts)
        sphinxopts.extend(["-q"])
        if self.language.tag != "en":
            locale_dirs = os.path.join(self.build_root, self.version.name, "locale")
            locale_clone_dir = os.path.join(
                locale_dirs, self.language.iso639_tag, "LC_MESSAGES"
            )
            locale_repo = "https://github.com/python/python-docs-{}.git".format(
                self.language.tag
            )
            git_clone(
                locale_repo,
                locale_clone_dir,
                translation_branch(locale_repo, locale_clone_dir, self.version.name),
            )
            sphinxopts.extend(
                (
                    "-D locale_dirs={}".format(locale_dirs),
                    "-D language={}".format(self.language.iso639_tag),
                    "-D gettext_compact=0",
                )
            )
        if self.version.status == "EOL":
            sphinxopts.append("-D html_context.outdated=1")
        git_clone(
            "https://github.com/python/cpython.git",
            self.checkout,
            self.version.branch_or_tag,
        )
        maketarget = (
            "autobuild-"
            + (
                "dev"
                if self.version.status in ("in development", "pre-release")
                else "stable"
            )
            + ("-html" if self.quick else "")
        )
        logging.info("Running make %s", maketarget)
        python = os.path.join(self.venv, "bin/python")
        sphinxbuild = os.path.join(self.venv, "bin/sphinx-build")
        blurb = os.path.join(self.venv, "bin/blurb")
        # Disable cpython switchers, we handle them now:
        run(
            [
                "sed",
                "-i",
                "s/ *-A switchers=1//",
                os.path.join(self.checkout, "Doc", "Makefile"),
            ]
        )
        setup_indexsidebar(
            os.path.join(
                self.checkout, "Doc", "tools", "templates", "indexsidebar.html"
            ),
            self.version,
        )
        run(
            [
                "make",
                "-C",
                os.path.join(self.checkout, "Doc"),
                "PYTHON=" + python,
                "SPHINXBUILD=" + sphinxbuild,
                "BLURB=" + blurb,
                "VENVDIR=" + self.venv,
                "SPHINXOPTS=" + " ".join(sphinxopts),
                "SPHINXERRORHANDLING=",
                maketarget,
            ]
        )
        run(["mkdir", "-p", self.log_directory])
        run(["chgrp", "-R", self.group, self.log_directory])
        setup_switchers(os.path.join(self.checkout, "Doc", "build", "html"))
        logging.info(
            "Build done for version: %s, language: %s",
            self.version.name,
            self.language.tag,
        )

    def build_venv(self):
        """Build a venv for the specific version.
        This is used to pin old Sphinx versions to old cpython branches.
        """
        venv_path = os.path.join(
            self.build_root, "venv-with-sphinx-" + self.version.sphinx_version
        )
        run(["python3", "-m", "venv", venv_path])
        run(
            [os.path.join(venv_path, "bin", "python"), "-m", "pip", "install"]
            + [self.theme]
            + self.version.requirements
        )
        self.venv = venv_path

    def copy_build_to_webroot(self):
        """Copy a given build to the appropriate webroot with appropriate rights."""
        logging.info(
            "Publishing start for version: %s, language: %s",
            self.version.name,
            self.language.tag,
        )
        Path(self.www_root).mkdir(parents=True, exist_ok=True)
        if self.language.tag == "en":
            target = os.path.join(self.www_root, self.version.name)
        else:
            language_dir = os.path.join(self.www_root, self.language.tag)
            os.makedirs(language_dir, exist_ok=True)
            try:
                run(["chgrp", "-R", self.group, language_dir])
            except subprocess.CalledProcessError as err:
                logging.warning("Can't change group of %s: %s", language_dir, str(err))
            os.chmod(language_dir, 0o775)
            target = os.path.join(language_dir, self.version.name)

        os.makedirs(target, exist_ok=True)
        try:
            os.chmod(target, 0o775)
        except PermissionError as err:
            logging.warning("Can't change mod of %s: %s", target, str(err))
        try:
            run(["chgrp", "-R", self.group, target])
        except subprocess.CalledProcessError as err:
            logging.warning("Can't change group of %s: %s", target, str(err))

        changed = changed_files(os.path.join(self.checkout, "Doc/build/html"), target)
        logging.info("Copying HTML files to %s", target)
        run(
            [
                "chown",
                "-R",
                ":" + self.group,
                os.path.join(self.checkout, "Doc/build/html/"),
            ]
        )
        run(["chmod", "-R", "o+r", os.path.join(self.checkout, "Doc/build/html/")])
        run(
            [
                "find",
                os.path.join(self.checkout, "Doc/build/html/"),
                "-type",
                "d",
                "-exec",
                "chmod",
                "o+x",
                "{}",
                ";",
            ]
        )
        if self.quick:
            run(["rsync", "-a", os.path.join(self.checkout, "Doc/build/html/"), target])
        else:
            run(
                [
                    "rsync",
                    "-a",
                    "--delete-delay",
                    "--filter",
                    "P archives/",
                    os.path.join(self.checkout, "Doc/build/html/"),
                    target,
                ]
            )
        if not self.quick:
            logging.debug("Copying dist files")
            run(
                [
                    "chown",
                    "-R",
                    ":" + self.group,
                    os.path.join(self.checkout, "Doc/dist/"),
                ]
            )
            run(
                [
                    "chmod",
                    "-R",
                    "o+r",
                    os.path.join(self.checkout, os.path.join("Doc/dist/")),
                ]
            )
            run(["mkdir", "-m", "o+rx", "-p", os.path.join(target, "archives")])
            run(["chown", ":" + self.group, os.path.join(target, "archives")])
            run(
                [
                    "cp",
                    "-a",
                    *[
                        str(dist)
                        for dist in (Path(self.checkout) / "Doc" / "dist").glob("*")
                    ],
                    os.path.join(target, "archives"),
                ]
            )
            changed.append("archives/")
            for fn in os.listdir(os.path.join(target, "archives")):
                changed.append("archives/" + fn)

        logging.info("%s files changed", len(changed))
        if changed and not self.skip_cache_invalidation:
            targets_dir = self.www_root
            prefixes = run(["find", "-L", targets_dir, "-samefile", target]).stdout
            prefixes = prefixes.replace(targets_dir + "/", "")
            prefixes = [prefix + "/" for prefix in prefixes.split("\n") if prefix]
            to_purge = prefixes[:]
            for prefix in prefixes:
                to_purge.extend(prefix + p for p in changed)
            logging.info("Running CDN purge")
            run(
                ["curl", "-XPURGE", "https://docs.python.org/{%s}" % ",".join(to_purge)]
            )
        logging.info(
            "Publishing done for version: %s, language: %s",
            self.version.name,
            self.language.tag,
        )


def symlink(www_root: Path, language: Language, directory: str, name: str, group: str):
    if language.tag == "en":  # english is rooted on /, no /en/
        path = www_root
    else:
        path = www_root / language.tag
    link = path / name
    directory_path = path / directory
    if not directory_path.exists():
        return  # No touching link, dest doc not built yet.
    if link.exists() and os.readlink(str(link)) == directory:
        return  # Link is already pointing to right doc.
    if link.exists():
        link.unlink()
    link.symlink_to(directory)
    run(["chown", "-h", ":" + group, str(link)])


def slash_3_symlink(languages, versions, www_root, group):
    """Maintains the /3/ symlinks for each languages.

    Like:
    - /3/ → /3.9/
    - /fr/3/ → /fr/3.9/
    - /es/3/ → /es/3.9/
    """
    www_root = Path(www_root)
    current_stable = Version.current_stable(versions).name
    for language in languages:
        symlink(www_root, language, current_stable, "3", group)


def dev_symlink(languages, versions, www_root, group):
    """Maintains the /dev/ symlinks for each languages.

    Like:
    - /dev/ → /3.11/
    - /fr/dev/ → /fr/3.11/
    - /es/dev/ → /es/3.11/
    """
    www_root = Path(www_root)
    current_dev = Version.current_dev(versions).name
    for language in languages:
        symlink(www_root, language, current_dev, "dev", group)


def main():
    args = parse_args()
    setup_logging(args.log_directory)
    languages_dict = {language.tag: language for language in LANGUAGES}
    versions = Version.filter(VERSIONS, args.branch)
    languages = [languages_dict[tag] for tag in args.languages]
    del args.languages
    del args.branch
    todo = list(product(versions, languages))
    while todo:
        version, language = todo.pop()
        if sentry_sdk:
            with sentry_sdk.configure_scope() as scope:
                scope.set_tag("version", version.name)
                scope.set_tag("language", language.tag)
        try:
            lock = zc.lockfile.LockFile(os.path.join(HERE, "build_docs.lock"))
            builder = DocBuilder(version, language, **vars(args))
            builder.run()
        except zc.lockfile.LockError:
            logging.info("Another builder is running... waiting...")
            time.sleep(10)
            todo.append((version, language))
        else:
            lock.close()

    build_sitemap(args.www_root)
    build_robots_txt(args.www_root, args.group, args.skip_cache_invalidation)
    slash_3_symlink(LANGUAGES, VERSIONS, args.www_root, args.group)
    dev_symlink(LANGUAGES, VERSIONS, args.www_root, args.group)


if __name__ == "__main__":
    main()
