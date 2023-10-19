#!/usr/bin/env python3

"""Build the Python docs for various branches and various languages.

Without any arguments builds docs for all active versions and
languages.

Languages are stored in `config.toml` while versions are discovered
from the devguide.

-q selects "quick build", which means to build only HTML.

Translations are fetched from github repositories according to PEP
545. `--languages` allows to select translations, like `--languages
en` to just build the english documents.

This script was originally created and by Georg Brandl in March
2010.
Modified by Benjamin Peterson to do CDN cache invalidation.
Modified by Julien Palard to build translations.

"""

from argparse import ArgumentParser
from contextlib import suppress, contextmanager
from dataclasses import dataclass
from datetime import datetime as dt, timezone
from time import perf_counter, sleep
import filecmp
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
from bisect import bisect_left as bisect
from collections import OrderedDict
from pathlib import Path
from string import Template
from textwrap import indent
from typing import Iterable
from urllib.parse import urljoin

import zc.lockfile
import jinja2
import requests
import tomlkit


try:
    from os import EX_OK, EX_SOFTWARE as EX_FAILURE
except ImportError:
    EX_OK, EX_FAILURE = 0, 1

try:
    import sentry_sdk
except ImportError:
    sentry_sdk = None
else:
    sentry_sdk.init()

HERE = Path(__file__).resolve().parent


@total_ordering
class Version:
    """Represents a cpython version and its documentation builds dependencies."""

    STATUSES = {"EOL", "security-fixes", "stable", "pre-release", "in development"}

    # Those synonyms map branch status vocabulary found in the devguide
    # with our vocabulary.
    SYNONYMS = {
        "feature": "in development",
        "bugfix": "stable",
        "security": "security-fixes",
        "end-of-life": "EOL",
    }

    def __init__(
        self,
        name,
        *,
        status,
        branch_or_tag=None,
    ):
        status = self.SYNONYMS.get(status, status)
        if status not in self.STATUSES:
            raise ValueError(
                "Version status expected to be one of: "
                f"{', '.join(self.STATUSES|set(self.SYNONYMS.keys()))}, got {status!r}."
            )
        self.name = name
        self.branch_or_tag = branch_or_tag
        self.status = status

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
    def current_stable(versions):
        """Find the current stable cPython version."""
        return max((v for v in versions if v.status == "stable"), key=Version.as_tuple)

    @staticmethod
    def current_dev(versions):
        """Find the current de cPython version."""
        return max(versions, key=Version.as_tuple)

    @property
    def picker_label(self):
        """Forge the label of a version picker."""
        if self.status == "in development":
            return f"dev ({self.name})"
        if self.status == "pre-release":
            return f"pre ({self.name})"
        return self.name

    def setup_indexsidebar(self, versions, dest_path):
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
                        versions, key=lambda v: version_to_tuple(v.name), reverse=True
                    ),
                )
            )

    @classmethod
    def from_json(cls, name, values):
        """Loads a version from devguide's json representation."""
        return cls(name, status=values["status"], branch_or_tag=values["branch"])

    def __eq__(self, other):
        return self.name == other.name

    def __gt__(self, other):
        return self.as_tuple() > other.as_tuple()


@dataclass(frozen=True, order=True)
class Language:
    iso639_tag: str
    name: str
    in_prod: bool
    sphinxopts: tuple
    html_only: bool = False

    @property
    def tag(self):
        return self.iso639_tag.replace("_", "-").lower()

    @staticmethod
    def filter(languages, language_tags=None):
        """Filter a sequence of languages according to --languages."""
        if language_tags:
            languages_dict = {language.tag: language for language in languages}
            return [languages_dict[tag] for tag in language_tags]
        return languages


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


@dataclass
class Repository:
    """Git repository abstraction for our specific needs."""

    remote: str
    directory: Path

    def fetch(self):
        """Try (and retry) to run git fetch."""
        try:
            return self.run("fetch")
        except subprocess.CalledProcessError as err:
            logging.error("'git fetch' failed (%s), retrying...", err.stderr)
            sleep(5)
        return self.run("fetch")

    def run(self, *args):
        """Run git command in the clone repository."""
        return run(("git", "-C", self.directory) + args)

    def get_ref(self, pattern):
        """Return the reference of a given tag or branch."""
        try:
            # Maybe it's a branch
            return self.run("show-ref", "-s", "origin/" + pattern).stdout.strip()
        except subprocess.CalledProcessError:
            # Maybe it's a tag
            return self.run("show-ref", "-s", "tags/" + pattern).stdout.strip()

    def switch(self, branch_or_tag):
        """Reset and cleans the repository to the given branch or tag."""
        self.run("reset", "--hard", self.get_ref(branch_or_tag), "--")
        self.run("clean", "-dfqx")

    def clone(self):
        """Maybe clone the repository, if not already cloned."""
        if (self.directory / ".git").is_dir():
            return False  # Already cloned
        logging.info("Cloning %s into %s", self.remote, self.directory)
        self.directory.mkdir(mode=0o775, parents=True, exist_ok=True)
        run(["git", "clone", self.remote, self.directory])
        return True

    def update(self):
        self.clone() or self.fetch()


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


def setup_switchers(
    versions: Iterable[Version], languages: Iterable[Language], html_root: Path
):
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
                                for language in languages
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
                                versions,
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


def build_robots_txt(
    versions: Iterable[Version],
    languages: Iterable[Language],
    www_root: Path,
    group,
    skip_cache_invalidation,
):
    """Disallow crawl of EOL versions in robots.txt."""
    if not www_root.exists():
        logging.info("Skipping robots.txt generation (www root does not even exists).")
        return
    robots_file = www_root / "robots.txt"
    with open(HERE / "templates" / "robots.txt", encoding="UTF-8") as template_file:
        template = jinja2.Template(template_file.read())
    with open(robots_file, "w", encoding="UTF-8") as robots_txt_file:
        robots_txt_file.write(
            template.render(languages=languages, versions=versions) + "\n"
        )
    robots_file.chmod(0o775)
    run(["chgrp", group, robots_file])
    if not skip_cache_invalidation:
        purge("robots.txt")


def build_sitemap(
    versions: Iterable[Version], languages: Iterable[Language], www_root: Path, group
):
    """Build a sitemap with all live versions and translations."""
    if not www_root.exists():
        logging.info("Skipping sitemap generation (www root does not even exists).")
        return
    with open(HERE / "templates" / "sitemap.xml", encoding="UTF-8") as template_file:
        template = jinja2.Template(template_file.read())
    sitemap_file = www_root / "sitemap.xml"
    sitemap_file.write_text(
        template.render(languages=languages, versions=versions) + "\n", encoding="UTF-8"
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
        metavar="3.12",
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
        help="Language translation, as a PEP 545 language tag like"
        " 'fr' or 'pt-br'. "
        "Builds all available languages by default.",
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
        logging.basicConfig(
            format="%(asctime)s %(levelname)s: %(message)s", stream=sys.stderr
        )
    else:
        log_directory.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.WatchedFileHandler(log_directory / "docsbuild.log")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
        )
        logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.DEBUG)


@dataclass
class DocBuilder:
    """Builder for a cpython version and a language."""

    version: Version
    versions: Iterable[Version]
    language: Language
    languages: Iterable[Language]
    cpython_repo: Repository
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

    def run(self) -> bool:
        """Build and publish a Python doc, for a language, and a version."""
        start_time = perf_counter()
        logging.info("Running.")
        try:
            self.cpython_repo.switch(self.version.branch_or_tag)
            if self.language.tag != "en":
                self.clone_translation()
            if self.should_rebuild():
                self.build_venv()
                self.build()
                self.copy_build_to_webroot()
                self.save_state(build_duration=perf_counter() - start_time)
        except Exception as err:
            logging.exception("Badly handled exception, human, please help.")
            if sentry_sdk:
                sentry_sdk.capture_exception(err)
            return False
        return True

    @property
    def checkout(self) -> Path:
        """Path to cpython git clone."""
        return self.build_root / "cpython"

    def clone_translation(self):
        self.translation_repo.update()
        self.translation_repo.switch(self.translation_branch)

    @property
    def translation_repo(self):
        """See PEP 545 for translations repository naming convention."""

        locale_repo = f"https://github.com/python/python-docs-{self.language.tag}.git"
        locale_clone_dir = (
            self.build_root
            / self.version.name
            / "locale"
            / self.language.iso639_tag
            / "LC_MESSAGES"
        )
        return Repository(locale_repo, locale_clone_dir)

    @property
    def translation_branch(self):
        """Some cpython versions may be untranslated, being either too old or
        too new.

        This function looks for remote branches on the given repo, and
        returns the name of the nearest existing branch.

        It could be enhanced to also search for tags.
        """
        remote_branches = self.translation_repo.run("branch", "-r").stdout
        branches = re.findall(r"/([0-9]+\.[0-9]+)$", remote_branches, re.M)
        return locate_nearest_version(branches, self.version.name)

    def build(self):
        """Build this version/language doc."""
        logging.info("Build start.")
        sphinxopts = list(self.language.sphinxopts)
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
            # Luatex already fixed this issue, so we can remove this once Texlive
            # is updated.
            # (https://github.com/TeX-Live/luatex/commit/af5faf1)
            subprocess.check_output(
                "sed -i s/\N{REPLACEMENT CHARACTER}/?/g "
                f"{locale_dirs}/ja/LC_MESSAGES/**/*.po",
                shell=True,
            )
            subprocess.check_output(
                "sed -i s/\N{REPLACEMENT CHARACTER}/?/g "
                f"{self.checkout}/Doc/**/*.rst",
                shell=True,
            )

        if self.version.status == "EOL":
            sphinxopts.append("-D html_context.outdated=1")
        maketarget = (
            "autobuild-"
            + (
                "dev"
                if self.version.status in ("in development", "pre-release")
                else "stable"
            )
            + ("" if self.full_build else "-html")
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
            self.versions,
            self.checkout / "Doc" / "tools" / "templates" / "indexsidebar.html",
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
        setup_switchers(
            self.versions, self.languages, self.checkout / "Doc" / "build" / "html"
        )
        logging.info("Build done.")

    def build_venv(self):
        """Build a venv for the specific Python version.

        So we can reuse them from builds to builds, while they contain
        different Sphinx versions.
        """
        venv_path = self.build_root / ("venv-" + self.version.name)
        run([sys.executable, "-m", "venv", venv_path])
        run(
            [venv_path / "bin" / "python", "-m", "pip", "install", "--upgrade"]
            + [self.theme]
            + self.version.requirements,
            cwd=self.checkout / "Doc",
        )
        run([venv_path / "bin" / "python", "-m", "pip", "freeze", "--all"])
        self.venv = venv_path

    def copy_build_to_webroot(self):
        """Copy a given build to the appropriate webroot with appropriate rights."""
        logging.info("Publishing start.")
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
            logging.debug("Copying dist files.")
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
            purge(*prefixes)
            for prefix in prefixes:
                purge(*[prefix + p for p in changed])
        logging.info("Publishing done")

    def should_rebuild(self):
        state = self.load_state()
        if not state:
            logging.info("Should rebuild: no previous state found.")
            return True
        cpython_sha = self.cpython_repo.run("rev-parse", "HEAD").stdout.strip()
        if self.language.tag != "en":
            translation_sha = self.translation_repo.run(
                "rev-parse", "HEAD"
            ).stdout.strip()
            if translation_sha != state["translation_sha"]:
                logging.info(
                    "Should rebuild: new translations (from %s to %s)",
                    state["translation_sha"],
                    translation_sha,
                )
                return True
        if cpython_sha != state["cpython_sha"]:
            diff = self.cpython_repo.run(
                "diff", "--name-only", state["cpython_sha"], cpython_sha
            ).stdout
            if "Doc/" in diff:
                logging.info(
                    "Should rebuild: Doc/ has changed (from %s to %s)",
                    state["cpython_sha"],
                    cpython_sha,
                )
                return True
        logging.info("Nothing changed, no rebuild needed.")
        return False

    def load_state(self) -> dict:
        state_file = self.build_root / "state.toml"
        try:
            return tomlkit.loads(state_file.read_text(encoding="UTF-8"))[
                f"/{self.language.tag}/{self.version.name}/"
            ]
        except KeyError:
            return {}

    def save_state(self, build_duration: float):
        """Save current cpython sha1 and current translation sha1.

        Using this we can deduce if a rebuild is needed or not.
        """
        state_file = self.build_root / "state.toml"
        try:
            states = tomlkit.parse(state_file.read_text(encoding="UTF-8"))
        except FileNotFoundError:
            states = tomlkit.document()

        state = {}
        state["cpython_sha"] = self.cpython_repo.run("rev-parse", "HEAD").stdout.strip()
        if self.language.tag != "en":
            state["translation_sha"] = self.translation_repo.run(
                "rev-parse", "HEAD"
            ).stdout.strip()
        state["last_build"] = dt.now(timezone.utc)
        state["last_build_duration"] = build_duration
        states[f"/{self.language.tag}/{self.version.name}/"] = state
        state_file.write_text(tomlkit.dumps(states), encoding="UTF-8")


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


def major_symlinks(
    www_root: Path, group, versions: Iterable[Version], languages: Iterable[Language]
):
    """Maintains the /2/ and /3/ symlinks for each languages.

    Like:
    - /3/ → /3.9/
    - /fr/3/ → /fr/3.9/
    - /es/3/ → /es/3.9/
    """
    current_stable = Version.current_stable(versions).name
    for language in languages:
        symlink(www_root, language, current_stable, "3", group)
        symlink(www_root, language, "2.7", "2", group)


def dev_symlink(www_root: Path, group, versions, languages):
    """Maintains the /dev/ symlinks for each languages.

    Like:
    - /dev/ → /3.11/
    - /fr/dev/ → /fr/3.11/
    - /es/dev/ → /es/3.11/
    """
    current_dev = Version.current_dev(versions).name
    for language in languages:
        symlink(www_root, language, current_dev, "dev", group)


def purge(*paths):
    """Remove one or many paths from docs.python.org's CDN.

    To be used when a file change, so the CDN fetch the new one.
    """
    base = "https://docs.python.org/"
    for path in paths:
        url = urljoin(base, str(path))
        logging.info("Purging %s from CDN", url)
        requests.request("PURGE", url, timeout=30)


def purge_path(www_root: Path, path: Path):
    """Recursively remove a path from docs.python.org's CDN.

    To be used when a directory change, so the CDN fetch the new one.
    """
    purge(*[file.relative_to(www_root) for file in path.glob("**/*")])
    purge(path.relative_to(www_root))
    purge(str(path.relative_to(www_root)) + "/")


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
                purge(str(file).replace("/srv/docs.python.org/", ""))


def parse_versions_from_devguide():
    releases = requests.get(
        "https://raw.githubusercontent.com/"
        "python/devguide/main/include/release-cycle.json",
        timeout=30,
    ).json()
    return [Version.from_json(name, release) for name, release in releases.items()]


def parse_languages_from_config():
    """Read config.toml to discover languages to build."""
    config = tomlkit.parse((HERE / "config.toml").read_text(encoding="UTF-8"))
    languages = []
    defaults = config["defaults"]
    for iso639_tag, section in config["languages"].items():
        languages.append(
            Language(
                iso639_tag,
                section["name"],
                section.get("in_prod", defaults["in_prod"]),
                sphinxopts=section.get("sphinxopts", defaults["sphinxopts"]),
                html_only=section.get("html_only", defaults["html_only"]),
            )
        )
    return languages


def build_docs(args) -> bool:
    """Build all docs (each languages and each versions)."""
    versions = parse_versions_from_devguide()
    languages = parse_languages_from_config()
    todo = [
        (version, language)
        for version in Version.filter(versions, args.branch)
        for language in Language.filter(languages, args.languages)
    ]
    del args.branch
    del args.languages
    all_built_successfully = True
    cpython_repo = Repository(
        "https://github.com/python/cpython.git", args.build_root / "cpython"
    )
    cpython_repo.update()
    while todo:
        version, language = todo.pop()
        logging.root.handlers[0].setFormatter(
            logging.Formatter(
                f"%(asctime)s %(levelname)s {language.tag}/{version.name}: %(message)s"
            )
        )
        if sentry_sdk:
            with sentry_sdk.configure_scope() as scope:
                scope.set_tag("version", version.name)
                scope.set_tag("language", language.tag)
        builder = DocBuilder(
            version, versions, language, languages, cpython_repo, **vars(args)
        )
        all_built_successfully &= builder.run()
    logging.root.handlers[0].setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
    )

    build_sitemap(versions, languages, args.www_root, args.group)
    build_404(args.www_root, args.group)
    build_robots_txt(
        versions, languages, args.www_root, args.group, args.skip_cache_invalidation
    )
    major_symlinks(args.www_root, args.group, versions, languages)
    dev_symlink(args.www_root, args.group, versions, languages)
    proofread_canonicals(args.www_root, args.skip_cache_invalidation)

    return all_built_successfully


def main():
    """Script entry point."""
    args = parse_args()
    setup_logging(args.log_directory)

    try:
        lock = zc.lockfile.LockFile(HERE / "build_docs.lock")
    except zc.lockfile.LockError:
        logging.info("Another builder is running... dying...")
        return EX_FAILURE

    try:
        return EX_OK if build_docs(args) else EX_FAILURE
    finally:
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
