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

from argparse import ArgumentParser
from contextlib import suppress
from dataclasses import dataclass
import filecmp
from itertools import product
import json
import logging
import logging.handlers
from functools import total_ordering
from os import readlink
import re
import shlex
import shutil
import subprocess
import sys
import time
from bisect import bisect_left as bisect
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from string import Template
from textwrap import indent

import zc.lockfile
import jinja2
import requests

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


@total_ordering
class Version:
    """Represents a cpython version and its documentation builds dependencies."""

    STATUSES = {"EOL", "security-fixes", "stable", "pre-release", "in development"}

    def __init__(
        self,
        name,
        *,
        status,
        branch=None,
        tag=None,
        sphinxopts=(),
    ):
        if status not in self.STATUSES:
            raise ValueError(
                f"Version status expected to be in {', '.join(self.STATUSES)}"
            )
        self.name = name
        if branch is not None and tag is not None:
            raise ValueError("Please build a version from either a branch or a tag.")
        if branch is None and tag is None:
            raise ValueError("Please build a version with at least a branch or a tag.")
        self.branch_or_tag = branch or tag
        self.status = status
        self.sphinxopts = list(sphinxopts)

    def __repr__(self):
        return f"Version({self.name})"

    @property
    def requirements(self):
        """Generate the right requirements for this version.

        Since CPython 3.8 a Doc/requirements.txt file can be used.

        In case the Doc/requirements.txt is absent or wrong (a
        sub-dependency broke), use this function to override it.

        See https://github.com/python/cpython/issues/91294
        See https://github.com/python/cpython/issues/91483

        """
        if self.name == "3.5":
            return ["jieba", "blurb", "sphinx==1.8.4", "jinja2<3.1", "docutils<=0.17.1"]
        if self.name in ("3.7", "3.6", "2.7"):
            return ["jieba", "blurb", "sphinx==2.3.1", "jinja2<3.1", "docutils<=0.17.1"]
        if self.name == ("3.8", "3.9"):
            return ["jieba", "blurb", "sphinx==2.4.4", "jinja2<3.1", "docutils<=0.17.1"]

        return [
            "jieba",  # To improve zh search.
            "-rrequirements.txt",
        ]

    @property
    def changefreq(self):
        """Estimate this version change frequency, for the sitemap."""
        return {"EOL": "never", "security-fixes": "yearly"}.get(self.status, "daily")

    def as_tuple(self):
        """This version name as tuple, for easy comparisons."""
        return version_to_tuple(self.name)

    @property
    def url(self):
        """The doc URL of this version in production."""
        return f"https://docs.python.org/{self.name}/"

    @property
    def title(self):
        """The title of this version's doc, for the sidebar."""
        return f"Python {self.name} ({self.status})"

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
    def current_stable():
        """Find the current stable cPython version."""
        return max([v for v in VERSIONS if v.status == "stable"], key=Version.as_tuple)

    @staticmethod
    def current_dev():
        """Find the current de cPython version."""
        return max(VERSIONS, key=Version.as_tuple)

    @property
    def picker_label(self):
        """Forge the label of a version picker."""
        if self.status == "in development":
            return f"dev ({self.name})"
        if self.status == "pre-release":
            return f"pre ({self.name})"
        return self.name

    def setup_indexsidebar(self, dest_path):
        """Build indexsidebar.html for Sphinx."""
        with open(
            HERE / "templates" / "indexsidebar.html", encoding="UTF-8"
        ) as sidebar_template_file:
            sidebar_template = jinja2.Template(sidebar_template_file.read())
        with open(dest_path, "w", encoding="UTF-8") as sidebar_file:
            sidebar_file.write(
                sidebar_template.render(
                    current_version=self,
                    versions=sorted(
                        VERSIONS, key=lambda v: version_to_tuple(v.name), reverse=True
                    ),
                )
            )

    def __eq__(self, other):
        return self.name == other.name

    def __gt__(self, other):
        return self.as_tuple() > other.as_tuple()



@dataclass(frozen=True, order=True)
class Language:
    tag: str
    iso639_tag: str
    name: str
    in_prod: bool
    sphinxopts: tuple
    html_only: bool = False


# EOL and security-fixes are not automatically built, no need to remove them
# from the list, this way we can still rebuild them manually as needed.
#
# Please keep the list in reverse-order for ease of editing.
VERSIONS = [
    Version("3.12", branch="origin/main", status="in development"),
    Version("3.11", branch="origin/3.11", status="stable"),
    Version("3.10", branch="origin/3.10", status="stable"),
    Version("3.9", branch="origin/3.9", status="security-fixes"),
    Version("3.8", branch="origin/3.8", status="security-fixes"),
    Version("3.7", branch="origin/3.7", status="security-fixes"),
    Version("3.6", tag="3.6", status="EOL"),
    Version("3.5", tag="3.5", status="EOL"),
    Version("2.7", tag="2.7", status="EOL"),
]

XELATEX_DEFAULT = (
    "-D latex_engine=xelatex",
    "-D latex_elements.inputenc=",
    "-D latex_elements.fontenc=",
)

LUALATEX_FOR_JP = (
    "-D latex_engine=lualatex",
    "-D latex_elements.inputenc=",
    "-D latex_elements.fontenc=",
    "-D latex_docclass.manual=ltjsbook",
    "-D latex_docclass.howto=ltjsarticle",

    # supress polyglossia warnings
    "-D latex_elements.polyglossia=",
    "-D latex_elements.fontpkg=",

    # preamble
    "-D latex_elements.preamble="

    # Render non-Japanese letters with luatex
    # https://gist.github.com/zr-tex8r/e0931df922f38fbb67634f05dfdaf66b
    r"\\usepackage[noto-otf]{luatexja-preset}"
    r"\\usepackage{newunicodechar}"
    r"\\newunicodechar{^^^^212a}{K}"

    # Workaround for the luatex-ja issue (Thanks to @jfbu)
    # https://github.com/sphinx-doc/sphinx/issues/11179#issuecomment-1420715092
    # https://osdn.net/projects/luatex-ja/ticket/47321
    r"\\makeatletter"
    r"\\titleformat{\\subsubsection}{\\normalsize\\py@HeaderFamily}"
    r"{\\py@TitleColor\\thesubsubsection}{0.5em}{\\py@TitleColor}"
    r"\\titleformat{\\paragraph}{\\normalsize\\py@HeaderFamily}"
    r"{\\py@TitleColor\\theparagraph}{0.5em}{\\py@TitleColor}"
    r"\\titleformat{\\subparagraph}{\\normalsize\\py@HeaderFamily}"
    r"{\\py@TitleColor\\thesubparagraph}{0.5em}{\\py@TitleColor}"
    r"\\makeatother"

    # subpress warning: (fancyhdr)Make it at least 16.4pt
    r"\\setlength{\\footskip}{16.4pt}"
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
    Language("it", "it", "Italian", False, XELATEX_DEFAULT),
    Language("ja", "ja", "Japanese", True, LUALATEX_FOR_JP),
    Language("ko", "ko", "Korean", True, XELATEX_FOR_KOREAN),
    Language("pl", "pl", "Polish", False, XELATEX_DEFAULT),
    Language("pt-br", "pt_BR", "Brazilian Portuguese", True, XELATEX_DEFAULT),
    Language("tr", "tr", "Turkish", True, XELATEX_DEFAULT),
    Language("uk", "uk", "Ukrainian", False, XELATEX_DEFAULT),
    Language("zh-cn", "zh_CN", "Simplified Chinese", True, XELATEX_WITH_CJK),
    Language("zh-tw", "zh_TW", "Traditional Chinese", True, XELATEX_WITH_CJK),
}


def run(cmd, cwd=None) -> subprocess.CompletedProcess:
    """Like subprocess.run, with logging before and after the command execution."""
    cmd = [str(arg) for arg in cmd]
    cmdstring = shlex.join(cmd)
    logging.debug("Run: %r", cmdstring)
    result = subprocess.run(
        cmd,
        cwd=cwd,
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


def git_clone(repository: str, directory: Path, branch_or_tag=None):
    """Clone or update the given repository in the given directory.
    Optionally checking out a branch.
    """
    logging.info("Updating repository %s in %s", repository, directory)
    try:
        if not (directory / ".git").is_dir():
            raise AssertionError("Not a git repository.")
        run(["git", "-C", directory, "fetch"])
        if branch_or_tag:
            run(["git", "-C", directory, "reset", "--hard", branch_or_tag, "--"])
            run(["git", "-C", directory, "clean", "-dfqx"])
    except (subprocess.CalledProcessError, AssertionError):
        if directory.exists():
            shutil.rmtree(directory)
        logging.info("Cloning %s into %s", repository, directory)
        directory.mkdir(mode=0o775, parents=True, exist_ok=True)
        run(["git", "clone", repository, directory])
        if branch_or_tag:
            run(["git", "-C", directory, "reset", "--hard", branch_or_tag, "--"])


def version_to_tuple(version):
    """Transform a version string to a tuple, for easy comparisons."""
    return tuple(int(part) for part in version.split("."))


def tuple_to_version(version_tuple):
    """Reverse version_to_tuple."""
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

    It could be enhanced to return tags, if needed, just return the
    tag as a string (without the `origin/` branch prefix).
    """
    git_clone(locale_repo, locale_clone_dir)
    remote_branches = run(["git", "-C", locale_clone_dir, "branch", "-r"]).stdout
    branches = re.findall(r"/([0-9]+\.[0-9]+)$", remote_branches, re.M)
    return "origin/" + locate_nearest_version(branches, needed_version)


@contextmanager
def edit(file: Path):
    """Context manager to edit a file "in place", use it as:

    with edit("/etc/hosts") as i, o:
        for line in i:
            o.write(line.replace("localhoat", "localhost"))
    """
    temporary = file.with_name(file.name + ".tmp")
    with suppress(FileNotFoundError):
        temporary.unlink()
    with open(file, encoding="UTF-8") as input_file:
        with open(temporary, "w", encoding="UTF-8") as output_file:
            yield input_file, output_file
    temporary.rename(file)


def setup_switchers(html_root: Path):
    """Setup cross-links between cpython versions:
    - Cross-link various languages in a language switcher
    - Cross-link various versions in a version switcher
    """
    with open(
        HERE / "templates" / "switchers.js", encoding="UTF-8"
    ) as switchers_template_file:
        template = Template(switchers_template_file.read())
    switchers_path = html_root / "_static" / "switchers.js"
    switchers_path.write_text(
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
                            (version.name, version.picker_label)
                            for version in sorted(
                                VERSIONS,
                                key=lambda v: version_to_tuple(v.name),
                                reverse=True,
                            )
                        ]
                    )
                ),
            }
        ),
        encoding="UTF-8",
    )
    for file in Path(html_root).glob("**/*.html"):
        depth = len(file.relative_to(html_root).parts) - 1
        src = f"{'../' * depth}_static/switchers.js"
        script = f'    <script type="text/javascript" src="{src}"></script>\n'
        with edit(file) as (ifile, ofile):
            for line in ifile:
                if line == script:
                    continue
                if line == "  </body>\n":
                    ofile.write(script)
                ofile.write(line)


def build_robots_txt(www_root: Path, group, skip_cache_invalidation):
    """Disallow crawl of EOL versions in robots.txt."""
    if not www_root.exists():
        logging.info("Skipping robots.txt generation (www root does not even exists).")
        return
    robots_file = www_root / "robots.txt"
    with open(HERE / "templates" / "robots.txt", encoding="UTF-8") as template_file:
        template = jinja2.Template(template_file.read())
    with open(robots_file, "w", encoding="UTF-8") as robots_txt_file:
        robots_txt_file.write(
            template.render(languages=LANGUAGES, versions=VERSIONS) + "\n"
        )
    robots_file.chmod(0o775)
    run(["chgrp", group, robots_file])
    if not skip_cache_invalidation:
        requests.request("PURGE", "https://docs.python.org/robots.txt")


def build_sitemap(www_root: Path, group):
    """Build a sitemap with all live versions and translations."""
    if not www_root.exists():
        logging.info("Skipping sitemap generation (www root does not even exists).")
        return
    with open(HERE / "templates" / "sitemap.xml", encoding="UTF-8") as template_file:
        template = jinja2.Template(template_file.read())
    sitemap_file = www_root / "sitemap.xml"
    sitemap_file.write_text(
        template.render(languages=LANGUAGES, versions=VERSIONS) + "\n", encoding="UTF-8"
    )
    sitemap_file.chmod(0o664)
    run(["chgrp", group, sitemap_file])


def build_404(www_root: Path, group):
    """Build a nice 404 error page to display in case PDFs are not built yet."""
    if not www_root.exists():
        logging.info("Skipping 404 page generation (www root does not even exists).")
        return
    not_found_file = www_root / "404.html"
    shutil.copyfile(HERE / "templates" / "404.html", not_found_file)
    not_found_file.chmod(0o664)
    run(["chgrp", group, not_found_file])


def head(text, lines=10):
    """Return the first *lines* lines from the given text."""
    return "\n".join(text.split("\n")[:lines])


def version_info():
    """Handler for --version."""
    try:
        platex_version = head(
            subprocess.check_output(["platex", "--version"], universal_newlines=True),
            lines=3,
        )
    except FileNotFoundError:
        platex_version = "Not installed."

    try:
        xelatex_version = head(
            subprocess.check_output(["xelatex", "--version"], universal_newlines=True),
            lines=2,
        )
    except FileNotFoundError:
        xelatex_version = "Not installed."
    print(
        f"""
# platex

{platex_version}


# xelatex

{xelatex_version}
    """
    )


def parse_args():
    """Parse command-line arguments."""

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
        type=Path,
        help="Path to a directory containing a checkout per branch.",
        default=Path("/srv/docsbuild"),
    )
    parser.add_argument(
        "-w",
        "--www-root",
        type=Path,
        help="Path where generated files will be copied.",
        default=Path("/srv/docs.python.org"),
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
        type=Path,
        help="Directory used to store logs.",
        default=Path("/var/log/docsbuild/"),
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
        args.log_directory = args.log_directory.resolve()
    if args.build_root:
        args.build_root = args.build_root.resolve()
    if args.www_root:
        args.www_root = args.www_root.resolve()
    return args


def setup_logging(log_directory: Path):
    """Setup logging to stderr if ran by a human, or to a file if ran from a cron."""
    if sys.stderr.isatty():
        logging.basicConfig(format="%(levelname)s:%(message)s", stream=sys.stderr)
    else:
        log_directory.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.WatchedFileHandler(log_directory / "docsbuild.log")
        handler.setFormatter(logging.Formatter("%(levelname)s:%(asctime)s:%(message)s"))
        logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.DEBUG)


@dataclass
class DocBuilder:
    """Builder for a cpython version and a language."""

    version: Version
    language: Language
    build_root: Path
    www_root: Path
    quick: bool
    group: str
    log_directory: Path
    skip_cache_invalidation: bool
    theme: Path

    @property
    def full_build(self):
        """Tell if a full build is needed.

        A full build is slow, it builds pdf, txt, epub, texinfo, and
        archives everything.

        A partial build only builds HTML and does not archive, it's
        fast.
        """
        return not self.quick and not self.language.html_only

    def run(self):
        """Build and publish a Python doc, for a language, and a version."""
        try:
            self.clone_cpython()
            if self.language.tag != "en":
                self.clone_translation()
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
    def checkout(self) -> Path:
        """Path to cpython git clone."""
        return self.build_root / "cpython"

    def clone_translation(self):
        locale_repo = f"https://github.com/python/python-docs-{self.language.tag}.git"
        locale_clone_dir = (
            self.build_root
            / self.version.name
            / "locale"
            / self.language.iso639_tag
            / "LC_MESSAGES"
        )
        git_clone(
            locale_repo,
            locale_clone_dir,
            translation_branch(locale_repo, locale_clone_dir, self.version.name),
        )

    def clone_cpython(self):
        git_clone(
            "https://github.com/python/cpython.git",
            self.checkout,
            self.version.branch_or_tag,
        )

    def build(self):
        """Build this version/language doc."""
        logging.info(
            "Build start for version: %s, language: %s",
            self.version.name,
            self.language.tag,
        )
        sphinxopts = list(self.language.sphinxopts) + list(self.version.sphinxopts)
        sphinxopts.extend(["-q"])
        if self.language.tag != "en":
            locale_dirs = self.build_root / self.version.name / "locale"
            sphinxopts.extend(
                (
                    f"-D locale_dirs={locale_dirs}",
                    f"-D language={self.language.iso639_tag}",
                    "-D gettext_compact=0",
                )
            )
        if self.language.tag == "ja":
            # Since luatex doesn't support \ufffd, replace \ufffd with '?'.
            # https://gist.github.com/zr-tex8r/e0931df922f38fbb67634f05dfdaf66b
            # Luatex already fixed this issue, so we can remove this once Texlive is updated.
            # (https://github.com/TeX-Live/luatex/commit/eaa95ce0a141eaf7a02)
            subprocess.check_output("sed -i s/\N{REPLACEMENT CHARACTER}/?/g "
                                    f"{locale_dirs}/ja/LC_MESSAGES/**/*.po",
                                    shell=True)
            subprocess.check_output("sed -i s/\N{REPLACEMENT CHARACTER}/?/g "
                                    f"{self.checkout}/Doc/**/*.rst", shell=True)

        if self.version.status == "EOL":
            sphinxopts.append("-D html_context.outdated=1")
        maketarget = (
            "autobuild-"
            + (
                "dev"
                if self.version.status in ("in development", "pre-release")
                else "stable"
            )
            + ("" if self.full_build  else "-html")
        )
        logging.info("Running make %s", maketarget)
        python = self.venv / "bin" / "python"
        sphinxbuild = self.venv / "bin" / "sphinx-build"
        blurb = self.venv / "bin" / "blurb"
        # Disable cpython switchers, we handle them now:
        run(
            [
                "sed",
                "-i",
                "s/ *-A switchers=1//",
                self.checkout / "Doc" / "Makefile",
            ]
        )
        self.version.setup_indexsidebar(
            self.checkout / "Doc" / "tools" / "templates" / "indexsidebar.html"
        )
        run(
            [
                "make",
                "-C",
                self.checkout / "Doc",
                "PYTHON=" + str(python),
                "SPHINXBUILD=" + str(sphinxbuild),
                "BLURB=" + str(blurb),
                "VENVDIR=" + str(self.venv),
                "SPHINXOPTS=" + " ".join(sphinxopts),
                "SPHINXERRORHANDLING=",
                maketarget,
            ]
        )
        run(["mkdir", "-p", self.log_directory])
        run(["chgrp", "-R", self.group, self.log_directory])
        setup_switchers(self.checkout / "Doc" / "build" / "html")
        logging.info(
            "Build done for version: %s, language: %s",
            self.version.name,
            self.language.tag,
        )

    def build_venv(self):
        """Build a venv for the specific Python version.

        So we can reuse them from builds to builds, while they contain
        different Sphinx versions.
        """
        venv_path = self.build_root / ("venv-" + self.version.name)
        run([sys.executable, "-m", "venv", venv_path])
        run(
            [venv_path / "bin" / "python", "-m", "pip", "install"]
            + [self.theme]
            + self.version.requirements,
            cwd=self.checkout / "Doc",
        )
        run([venv_path / "bin" / "python", "-m", "pip", "freeze", "--all"])
        self.venv = venv_path

    def copy_build_to_webroot(self):
        """Copy a given build to the appropriate webroot with appropriate rights."""
        logging.info(
            "Publishing start for version: %s, language: %s",
            self.version.name,
            self.language.tag,
        )
        self.www_root.mkdir(parents=True, exist_ok=True)
        if self.language.tag == "en":
            target = self.www_root / self.version.name
        else:
            language_dir = self.www_root / self.language.tag
            language_dir.mkdir(parents=True, exist_ok=True)
            try:
                run(["chgrp", "-R", self.group, language_dir])
            except subprocess.CalledProcessError as err:
                logging.warning("Can't change group of %s: %s", language_dir, str(err))
            language_dir.chmod(0o775)
            target = language_dir / self.version.name

        target.mkdir(parents=True, exist_ok=True)
        try:
            target.chmod(0o775)
        except PermissionError as err:
            logging.warning("Can't change mod of %s: %s", target, str(err))
        try:
            run(["chgrp", "-R", self.group, target])
        except subprocess.CalledProcessError as err:
            logging.warning("Can't change group of %s: %s", target, str(err))

        changed = changed_files(self.checkout / "Doc" / "build" / "html", target)
        logging.info("Copying HTML files to %s", target)
        run(
            [
                "chown",
                "-R",
                ":" + self.group,
                self.checkout / "Doc" / "build" / "html/",
            ]
        )
        run(["chmod", "-R", "o+r", self.checkout / "Doc" / "build" / "html"])
        run(
            [
                "find",
                self.checkout / "Doc" / "build" / "html",
                "-type",
                "d",
                "-exec",
                "chmod",
                "o+x",
                "{}",
                ";",
            ]
        )
        if self.full_build:
            run(
                [
                    "rsync",
                    "-a",
                    "--delete-delay",
                    "--filter",
                    "P archives/",
                    str(self.checkout / "Doc" / "build" / "html") + "/",
                    target,
                ]
            )
        else:
            run(
                [
                    "rsync",
                    "-a",
                    str(self.checkout / "Doc" / "build" / "html") + "/",
                    target,
                ]
            )
        if self.full_build:
            logging.debug("Copying dist files")
            run(
                [
                    "chown",
                    "-R",
                    ":" + self.group,
                    self.checkout / "Doc" / "dist",
                ]
            )
            run(
                [
                    "chmod",
                    "-R",
                    "o+r",
                    self.checkout / "Doc" / "dist",
                ]
            )
            run(["mkdir", "-m", "o+rx", "-p", target / "archives"])
            run(["chown", ":" + self.group, target / "archives"])
            run(
                [
                    "cp",
                    "-a",
                    *[
                        str(dist)
                        for dist in (Path(self.checkout) / "Doc" / "dist").glob("*")
                    ],
                    target / "archives",
                ]
            )
            changed.append("archives/")
            for file in (target / "archives").iterdir():
                changed.append("archives/" + file.name)

        logging.info("%s files changed", len(changed))
        if changed and not self.skip_cache_invalidation:
            targets_dir = str(self.www_root)
            prefixes = run(["find", "-L", targets_dir, "-samefile", target]).stdout
            prefixes = prefixes.replace(targets_dir + "/", "")
            prefixes = [prefix + "/" for prefix in prefixes.split("\n") if prefix]
            to_purge = prefixes[:]
            for prefix in prefixes:
                to_purge.extend(prefix + p for p in changed)
            logging.info("Running CDN purge")
            run(
                ["curl", "-XPURGE", f"https://docs.python.org/{{{','.join(to_purge)}}}"]
            )
        logging.info(
            "Publishing done for version: %s, language: %s",
            self.version.name,
            self.language.tag,
        )


def symlink(www_root: Path, language: Language, directory: str, name: str, group: str):
    """Used by major_symlinks and dev_symlink to maintain symlinks."""
    if language.tag == "en":  # english is rooted on /, no /en/
        path = www_root
    else:
        path = www_root / language.tag
    link = path / name
    directory_path = path / directory
    if not directory_path.exists():
        return  # No touching link, dest doc not built yet.
    if link.exists() and readlink(str(link)) == directory:
        return  # Link is already pointing to right doc.
    if link.exists():
        link.unlink()
    link.symlink_to(directory)
    run(["chown", "-h", ":" + group, str(link)])
    purge_path(www_root, link)


def major_symlinks(www_root: Path, group):
    """Maintains the /2/ and /3/ symlinks for each languages.

    Like:
    - /3/ → /3.9/
    - /fr/3/ → /fr/3.9/
    - /es/3/ → /es/3.9/
    """
    current_stable = Version.current_stable().name
    for language in LANGUAGES:
        symlink(www_root, language, current_stable, "3", group)
        symlink(www_root, language, "2.7", "2", group)


def dev_symlink(www_root: Path, group):
    """Maintains the /dev/ symlinks for each languages.

    Like:
    - /dev/ → /3.11/
    - /fr/dev/ → /fr/3.11/
    - /es/dev/ → /es/3.11/
    """
    current_dev = Version.current_dev().name
    for language in LANGUAGES:
        symlink(www_root, language, current_dev, "dev", group)


def proofread_canonicals(www_root: Path, skip_cache_invalidation: bool) -> None:
    """In www_root we check that all canonical links point to existing contents.

    It can happen that a canonical is "broken":

    - /3.11/whatsnew/3.11.html typically would link to
    /3/whatsnew/3.11.html, which may not exist yet.
    """
    canonical_re = re.compile(
        """<link rel="canonical" href="https://docs.python.org/([^"]*)" />"""
    )
    for file in www_root.glob("**/*.html"):
        html = file.read_text(encoding="UTF-8", errors="surrogateescape")
        canonical = canonical_re.search(html)
        if not canonical:
            continue
        target = canonical.group(1)
        if not (www_root / target).exists():
            logging.info("Removing broken canonical from %s to %s", file, target)
            html = html.replace(canonical.group(0), "")
            file.write_text(html, encoding="UTF-8", errors="surrogateescape")
            if not skip_cache_invalidation:
                url = str(file).replace("/srv/", "https://")
                logging.info("Purging %s from CDN", url)
                requests.request("PURGE", url)


def purge_path(www_root: Path, path: Path):
    to_purge = [str(file.relative_to(www_root)) for file in path.glob("**/*")]
    to_purge.append(str(path.relative_to(www_root)))
    to_purge.append(str(path.relative_to(www_root)) + "/")
    run(["curl", "-XPURGE", f"https://docs.python.org/{{{','.join(to_purge)}}}"])


def main() -> None:
    """Script entry point."""
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
            lock = zc.lockfile.LockFile(HERE / "build_docs.lock")
            builder = DocBuilder(version, language, **vars(args))
            builder.run()
        except zc.lockfile.LockError:
            logging.info("Another builder is running... waiting...")
            time.sleep(10)
            todo.append((version, language))
        else:
            lock.close()

    build_sitemap(args.www_root, args.group)
    build_404(args.www_root, args.group)
    build_robots_txt(args.www_root, args.group, args.skip_cache_invalidation)
    major_symlinks(args.www_root, args.group)
    dev_symlink(args.www_root, args.group)
    proofread_canonicals(args.www_root, args.skip_cache_invalidation)


if __name__ == "__main__":
    main()
