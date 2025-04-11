#!/usr/bin/env python3

"""Build the Python docs for various branches and various languages.

Without any arguments builds docs for all active versions and
languages.

Languages are stored in `config.toml` while versions are discovered
from the devguide.

-q selects "quick build", which means to build only HTML.

Translations are fetched from GitHub repositories according to PEP
545. `--languages` allows selecting translations, like `--languages
en` to just build the English documents.

This script was originally created by Georg Brandl in March 2010.
Modified by Benjamin Peterson to do CDN cache invalidation.
Modified by Julien Palard to build translations.

"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import datetime as dt
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
from contextlib import contextmanager, suppress
from functools import total_ordering
from pathlib import Path
from string import Template
from time import perf_counter, sleep
from urllib.parse import urljoin

import jinja2
import tomlkit
import urllib3
import zc.lockfile

TYPE_CHECKING = False
if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence, Set
    from typing import Literal

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


@dataclasses.dataclass(frozen=True, slots=True)
class Versions:
    _seq: Sequence[Version]

    def __iter__(self) -> Iterator[Version]:
        return iter(self._seq)

    def __reversed__(self) -> Iterator[Version]:
        return reversed(self._seq)

    @classmethod
    def from_json(cls, data) -> Versions:
        versions = sorted(
            [Version.from_json(name, release) for name, release in data.items()],
            key=Version.as_tuple,
        )
        return cls(versions)

    def filter(self, branch: str = "") -> Sequence[Version]:
        """Filter the given versions.

        If *branch* is given, only *versions* matching *branch* are returned.

        Else all live versions are returned (this means no EOL and no
        security-fixes branches).
        """
        if branch:
            return [v for v in self if branch in (v.name, v.branch_or_tag)]
        return [v for v in self if v.status not in {"EOL", "security-fixes"}]

    @property
    def current_stable(self) -> Version:
        """Find the current stable CPython version."""
        return max((v for v in self if v.status == "stable"), key=Version.as_tuple)

    @property
    def current_dev(self) -> Version:
        """Find the current CPython version in development."""
        return max(self, key=Version.as_tuple)

    def setup_indexsidebar(self, current: Version, dest_path: Path) -> None:
        """Build indexsidebar.html for Sphinx."""
        template_path = HERE / "templates" / "indexsidebar.html"
        template = jinja2.Template(template_path.read_text(encoding="UTF-8"))
        rendered_template = template.render(
            current_version=current,
            versions=list(reversed(self)),
        )
        dest_path.write_text(rendered_template, encoding="UTF-8")


@total_ordering
class Version:
    """Represents a CPython version and its documentation build dependencies."""

    STATUSES = {"EOL", "security-fixes", "stable", "pre-release", "in development"}

    # Those synonyms map branch status vocabulary found in the devguide
    # with our vocabulary.
    SYNONYMS = {
        "feature": "in development",
        "bugfix": "stable",
        "security": "security-fixes",
        "end-of-life": "EOL",
        "prerelease": "pre-release",
    }

    def __init__(self, name, *, status, branch_or_tag=None):
        status = self.SYNONYMS.get(status, status)
        if status not in self.STATUSES:
            raise ValueError(
                "Version status expected to be one of: "
                f"{', '.join(self.STATUSES | set(self.SYNONYMS.keys()))}, got {status!r}."
            )
        self.name = name
        self.branch_or_tag = branch_or_tag
        self.status = status

    def __repr__(self):
        return f"Version({self.name})"

    def __eq__(self, other):
        return self.name == other.name

    def __gt__(self, other):
        return self.as_tuple() > other.as_tuple()

    @classmethod
    def from_json(cls, name, values):
        """Loads a version from devguide's json representation."""
        return cls(name, status=values["status"], branch_or_tag=values["branch"])

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
        if self.name in {"3.7", "3.6", "2.7"}:
            return ["jieba", "blurb", "sphinx==2.3.1", "jinja2<3.1", "docutils<=0.17.1"]

        return [
            "jieba",  # To improve zh search.
            "PyStemmer~=2.2.0",  # To improve performance for word stemming.
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

    @property
    def picker_label(self):
        """Forge the label of a version picker."""
        if self.status == "in development":
            return f"dev ({self.name})"
        if self.status == "pre-release":
            return f"pre ({self.name})"
        return self.name


@dataclasses.dataclass(frozen=True, slots=True)
class Languages:
    _seq: Sequence[Language]

    def __iter__(self) -> Iterator[Language]:
        return iter(self._seq)

    def __reversed__(self) -> Iterator[Language]:
        return reversed(self._seq)

    @classmethod
    def from_json(cls, defaults, languages) -> Languages:
        default_translated_name = defaults.get("translated_name", "")
        default_in_prod = defaults.get("in_prod", True)
        default_sphinxopts = defaults.get("sphinxopts", [])
        default_html_only = defaults.get("html_only", False)
        langs = [
            Language(
                iso639_tag=iso639_tag,
                name=section["name"],
                translated_name=section.get("translated_name", default_translated_name),
                in_prod=section.get("in_prod", default_in_prod),
                sphinxopts=section.get("sphinxopts", default_sphinxopts),
                html_only=section.get("html_only", default_html_only),
            )
            for iso639_tag, section in languages.items()
        ]
        return cls(langs)

    def filter(self, language_tags: Sequence[str] = ()) -> Sequence[Language]:
        """Filter a sequence of languages according to --languages."""
        if language_tags:
            language_tags = frozenset(language_tags)
            return [l for l in self if l.tag in language_tags]
        return list(self)


@dataclasses.dataclass(order=True, frozen=True, kw_only=True)
class Language:
    iso639_tag: str
    name: str
    translated_name: str
    in_prod: bool
    sphinxopts: Sequence[str]
    html_only: bool = False

    @property
    def tag(self):
        return self.iso639_tag.replace("_", "-").lower()

    @property
    def switcher_label(self):
        if self.translated_name:
            return f"{self.name} | {self.translated_name}"
        return self.name


def run(cmd, cwd=None) -> subprocess.CompletedProcess:
    """Like subprocess.run, with logging before and after the command execution."""
    cmd = list(map(str, cmd))
    cmdstring = shlex.join(cmd)
    logging.debug("Run: '%s'", cmdstring)
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
            "Run: '%s' KO:\n%s",
            cmdstring,
            "\n".join(f"    {line}" for line in result.stdout.split("\n")[-20:]),
        )
    result.check_returncode()
    return result


def run_with_logging(cmd, cwd=None):
    """Like subprocess.check_call, with logging before the command execution."""
    cmd = list(map(str, cmd))
    logging.debug("Run: '%s'", shlex.join(cmd))
    with subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdout=subprocess.PIPE,
        encoding="utf-8",
    ) as p:
        try:
            for line in p.stdout or ():
                logging.debug(">>>> %s", line.rstrip())
        except:
            p.kill()
            raise
    if return_code := p.poll():
        raise subprocess.CalledProcessError(return_code, cmd[0])


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


@dataclasses.dataclass
class Repository:
    """Git repository abstraction for our specific needs."""

    remote: str
    directory: Path

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

    def fetch(self):
        """Try (and retry) to run git fetch."""
        try:
            return self.run("fetch")
        except subprocess.CalledProcessError as err:
            logging.error("'git fetch' failed (%s), retrying...", err.stderr)
            sleep(5)
        return self.run("fetch")

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

    available_versions_tuples = sorted(map(version_to_tuple, set(available_versions)))
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

    with edit("/etc/hosts") as (i, o):
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


def setup_switchers(versions: Versions, languages: Languages, html_root: Path):
    """Setup cross-links between CPython versions:
    - Cross-link various languages in a language switcher
    - Cross-link various versions in a version switcher
    """
    language_pairs = sorted((l.tag, l.switcher_label) for l in languages if l.in_prod)
    version_pairs = [(v.name, v.picker_label) for v in reversed(versions)]

    switchers_template_file = HERE / "templates" / "switchers.js"
    switchers_path = html_root / "_static" / "switchers.js"

    template = Template(switchers_template_file.read_text(encoding="UTF-8"))
    rendered_template = template.safe_substitute(
        LANGUAGES=json.dumps(language_pairs),
        VERSIONS=json.dumps(version_pairs),
    )
    switchers_path.write_text(rendered_template, encoding="UTF-8")

    for file in html_root.glob("**/*.html"):
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


def head(text, lines=10):
    """Return the first *lines* lines from the given text."""
    return "\n".join(text.split("\n")[:lines])


def version_info():
    """Handler for --version."""
    try:
        platex_version = head(
            subprocess.check_output(["platex", "--version"], text=True),
            lines=3,
        )
    except FileNotFoundError:
        platex_version = "Not installed."

    try:
        xelatex_version = head(
            subprocess.check_output(["xelatex", "--version"], text=True),
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


@dataclasses.dataclass
class DocBuilder:
    """Builder for a CPython version and a language."""

    version: Version
    versions: Versions
    language: Language
    languages: Languages
    cpython_repo: Repository
    build_root: Path
    www_root: Path
    select_output: Literal["no-html", "only-html", "only-html-en"] | None
    quick: bool
    group: str
    log_directory: Path
    skip_cache_invalidation: bool
    theme: Path

    @property
    def html_only(self):
        return (
            self.select_output in {"only-html", "only-html-en"}
            or self.quick
            or self.language.html_only
        )

    @property
    def includes_html(self):
        """Does the build we are running include HTML output?"""
        return self.select_output != "no-html"

    def run(self, http: urllib3.PoolManager) -> bool | None:
        """Build and publish a Python doc, for a language, and a version."""
        start_time = perf_counter()
        start_timestamp = dt.datetime.now(tz=dt.UTC).replace(microsecond=0)
        logging.info("Running.")
        try:
            if self.language.html_only and not self.includes_html:
                logging.info("Skipping non-HTML build (language is HTML-only).")
                return None  # skipped
            self.cpython_repo.switch(self.version.branch_or_tag)
            if self.language.tag != "en":
                self.clone_translation()
            if trigger_reason := self.should_rebuild():
                self.build_venv()
                self.build()
                self.copy_build_to_webroot(http)
                self.save_state(
                    build_start=start_timestamp,
                    build_duration=perf_counter() - start_time,
                    trigger=trigger_reason,
                )
            else:
                return None  # skipped
        except Exception as err:
            logging.exception("Badly handled exception, human, please help.")
            if sentry_sdk:
                sentry_sdk.capture_exception(err)
            return False
        return True

    @property
    def checkout(self) -> Path:
        """Path to CPython git clone."""
        return self.build_root / _checkout_name(self.select_output)

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
        """Some CPython versions may be untranslated, being either too old or
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
        start_time = perf_counter()
        sphinxopts = list(self.language.sphinxopts)
        if self.language.tag != "en":
            locale_dirs = self.build_root / self.version.name / "locale"
            sphinxopts.extend((
                f"-D locale_dirs={locale_dirs}",
                f"-D language={self.language.iso639_tag}",
                "-D gettext_compact=0",
                "-D translation_progress_classes=1",
            ))
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
                f"sed -i s/\N{REPLACEMENT CHARACTER}/?/g {self.checkout}/Doc/**/*.rst",
                shell=True,
            )

        if self.version.status == "EOL":
            sphinxopts.append("-D html_context.outdated=1")

        if self.version.status in ("in development", "pre-release"):
            maketarget = "autobuild-dev"
        else:
            maketarget = "autobuild-stable"
        if self.html_only:
            maketarget += "-html"
        logging.info("Running make %s", maketarget)
        python = self.venv / "bin" / "python"
        sphinxbuild = self.venv / "bin" / "sphinx-build"
        blurb = self.venv / "bin" / "blurb"

        if self.includes_html:
            site_url = self.version.url
            if self.language.tag != "en":
                site_url += f"{self.language.tag}/"
            # Define a tag to enable opengraph socialcards previews
            # (used in Doc/conf.py and requires matplotlib)
            sphinxopts += (
                "-t create-social-cards",
                f"-D ogp_site_url={site_url}",
            )

            # Disable CPython switchers, we handle them now:
            run(
                ["sed", "-i"]
                + ([""] if sys.platform == "darwin" else [])
                + ["s/ *-A switchers=1//", self.checkout / "Doc" / "Makefile"]
            )
            self.versions.setup_indexsidebar(
                self.version,
                self.checkout / "Doc" / "tools" / "templates" / "indexsidebar.html",
            )
        run_with_logging([
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
        ])
        run(["mkdir", "-p", self.log_directory])
        run(["chgrp", "-R", self.group, self.log_directory])
        if self.includes_html:
            setup_switchers(
                self.versions, self.languages, self.checkout / "Doc" / "build" / "html"
            )
        logging.info("Build done (%s).", format_seconds(perf_counter() - start_time))

    def build_venv(self):
        """Build a venv for the specific Python version.

        So we can reuse them from builds to builds, while they contain
        different Sphinx versions.
        """
        requirements = [self.theme] + self.version.requirements
        if self.includes_html:
            # opengraph previews
            requirements.append("matplotlib>=3")

        venv_path = self.build_root / ("venv-" + self.version.name)
        run([sys.executable, "-m", "venv", venv_path])
        run(
            [venv_path / "bin" / "python", "-m", "pip", "install", "--upgrade"]
            + ["--upgrade-strategy=eager"]
            + requirements,
            cwd=self.checkout / "Doc",
        )
        run([venv_path / "bin" / "python", "-m", "pip", "freeze", "--all"])
        self.venv = venv_path

    def copy_build_to_webroot(self, http: urllib3.PoolManager) -> None:
        """Copy a given build to the appropriate webroot with appropriate rights."""
        logging.info("Publishing start.")
        start_time = perf_counter()
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

        changed = []
        if self.includes_html:
            # Copy built HTML files to webroot (default /srv/docs.python.org)
            changed = changed_files(self.checkout / "Doc" / "build" / "html", target)
            logging.info("Copying HTML files to %s", target)
            run([
                "chown",
                "-R",
                ":" + self.group,
                self.checkout / "Doc" / "build" / "html/",
            ])
            run(["chmod", "-R", "o+r", self.checkout / "Doc" / "build" / "html"])
            run([
                "find",
                self.checkout / "Doc" / "build" / "html",
                "-type",
                "d",
                "-exec",
                "chmod",
                "o+x",
                "{}",
                ";",
            ])
            run([
                "rsync",
                "-a",
                "--delete-delay",
                "--filter",
                "P archives/",
                str(self.checkout / "Doc" / "build" / "html") + "/",
                target,
            ])

        if not self.quick:
            # Copy archive files to /archives/
            logging.debug("Copying dist files.")
            run([
                "chown",
                "-R",
                ":" + self.group,
                self.checkout / "Doc" / "dist",
            ])
            run([
                "chmod",
                "-R",
                "o+r",
                self.checkout / "Doc" / "dist",
            ])
            run(["mkdir", "-m", "o+rx", "-p", target / "archives"])
            run(["chown", ":" + self.group, target / "archives"])
            run([
                "cp",
                "-a",
                *(self.checkout / "Doc" / "dist").glob("*"),
                target / "archives",
            ])
            changed.append("archives/")
            for file in (target / "archives").iterdir():
                changed.append("archives/" + file.name)

        logging.info("%s files changed", len(changed))
        if changed and not self.skip_cache_invalidation:
            surrogate_key = f"{self.language.tag}/{self.version.name}"
            purge_surrogate_key(http, surrogate_key)
        logging.info(
            "Publishing done (%s).", format_seconds(perf_counter() - start_time)
        )

    def should_rebuild(self):
        state = self.load_state()
        if not state:
            logging.info("Should rebuild: no previous state found.")
            return "no previous state"
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
                return "new translations"
        if cpython_sha != state["cpython_sha"]:
            diff = self.cpython_repo.run(
                "diff", "--name-only", state["cpython_sha"], cpython_sha
            ).stdout
            if "Doc/" in diff or "Misc/NEWS.d/" in diff:
                logging.info(
                    "Should rebuild: Doc/ has changed (from %s to %s)",
                    state["cpython_sha"],
                    cpython_sha,
                )
                return "Doc/ has changed"
        logging.info("Nothing changed, no rebuild needed.")
        return False

    def load_state(self) -> dict:
        if self.select_output is not None:
            state_file = self.build_root / f"state-{self.select_output}.toml"
        else:
            state_file = self.build_root / "state.toml"
        try:
            return tomlkit.loads(state_file.read_text(encoding="UTF-8"))[
                f"/{self.language.tag}/{self.version.name}/"
            ]
        except (KeyError, FileNotFoundError):
            return {}

    def save_state(self, build_start: dt.datetime, build_duration: float, trigger: str):
        """Save current CPython sha1 and current translation sha1.

        Using this we can deduce if a rebuild is needed or not.
        """
        if self.select_output is not None:
            state_file = self.build_root / f"state-{self.select_output}.toml"
        else:
            state_file = self.build_root / "state.toml"
        try:
            states = tomlkit.parse(state_file.read_text(encoding="UTF-8"))
        except FileNotFoundError:
            states = tomlkit.document()

        key = f"/{self.language.tag}/{self.version.name}/"
        state = {
            "last_build_start": build_start,
            "last_build_duration": round(build_duration, 0),
            "triggered_by": trigger,
            "cpython_sha": self.cpython_repo.run("rev-parse", "HEAD").stdout.strip(),
        }
        if self.language.tag != "en":
            state["translation_sha"] = self.translation_repo.run(
                "rev-parse", "HEAD"
            ).stdout.strip()
        states[key] = state
        state_file.write_text(tomlkit.dumps(states), encoding="UTF-8")

        table = tomlkit.inline_table()
        table |= state
        logging.info("Saved new rebuild state for %s: %s", key, table.as_string())


def format_seconds(seconds: float) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    hours, minutes, seconds = int(hours), int(minutes), round(seconds)

    match (hours, minutes, seconds):
        case 0, 0, s:
            return f"{s}s"
        case 0, m, s:
            return f"{m}m {s}s"
        case h, m, s:
            return f"{h}h {m}m {s}s"


def _checkout_name(select_output: str | None) -> str:
    if select_output is not None:
        return f"cpython-{select_output}"
    return "cpython"


def main():
    """Script entry point."""
    args = parse_args()
    setup_logging(args.log_directory, args.select_output)

    if args.select_output is None:
        build_docs_with_lock(args, "build_docs.lock")
    elif args.select_output == "no-html":
        build_docs_with_lock(args, "build_docs_archives.lock")
    elif args.select_output == "only-html":
        build_docs_with_lock(args, "build_docs_html.lock")
    elif args.select_output == "only-html-en":
        build_docs_with_lock(args, "build_docs_html_en.lock")


def parse_args():
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Runs a build of the Python docs for various branches.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--select-output",
        choices=("no-html", "only-html", "only-html-en"),
        help="Choose what outputs to build.",
    )
    parser.add_argument(
        "-q",
        "--quick",
        action="store_true",
        help="Run a quick build (only HTML files).",
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
        help="Skip Fastly cache invalidation.",
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
        "--language",
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
        help="Python package to use for python-docs-theme: Useful to test branches:"
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


def setup_logging(log_directory: Path, select_output: str | None):
    """Setup logging to stderr if run by a human, or to a file if run from a cron."""
    log_format = "%(asctime)s %(levelname)s: %(message)s"
    if sys.stderr.isatty():
        logging.basicConfig(format=log_format, stream=sys.stderr)
    else:
        log_directory.mkdir(parents=True, exist_ok=True)
        if select_output is None:
            filename = log_directory / "docsbuild.log"
        else:
            filename = log_directory / f"docsbuild-{select_output}.log"
        handler = logging.handlers.WatchedFileHandler(filename)
        handler.setFormatter(logging.Formatter(log_format))
        logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.DEBUG)


def build_docs_with_lock(args: argparse.Namespace, lockfile_name: str) -> int:
    try:
        lock = zc.lockfile.LockFile(HERE / lockfile_name)
    except zc.lockfile.LockError:
        logging.info("Another builder is running... dying...")
        return EX_FAILURE

    try:
        return EX_OK if build_docs(args) else EX_FAILURE
    finally:
        lock.close()


def build_docs(args: argparse.Namespace) -> bool:
    """Build all docs (each language and each version)."""
    logging.info("Full build start.")
    start_time = perf_counter()
    http = urllib3.PoolManager()
    versions = parse_versions_from_devguide(http)
    languages = parse_languages_from_config()
    # Reverse languages but not versions, because we take version-language
    # pairs from the end of the list, effectively reversing it.
    # This runs languages in config.toml order and versions newest first.
    todo = [
        (version, language)
        for version in versions.filter(args.branch)
        for language in reversed(languages.filter(args.languages))
    ]
    del args.branch
    del args.languages

    build_succeeded = set()
    build_failed = set()
    cpython_repo = Repository(
        "https://github.com/python/cpython.git",
        args.build_root / _checkout_name(args.select_output),
    )
    while todo:
        version, language = todo.pop()
        logging.root.handlers[0].setFormatter(
            logging.Formatter(
                f"%(asctime)s %(levelname)s {language.tag}/{version.name}: %(message)s"
            )
        )
        if sentry_sdk:
            scope = sentry_sdk.get_isolation_scope()
            scope.set_tag("version", version.name)
            scope.set_tag("language", language.tag)
            cpython_repo.update()
        builder = DocBuilder(
            version, versions, language, languages, cpython_repo, **vars(args)
        )
        built_successfully = builder.run(http)
        if built_successfully:
            build_succeeded.add((version.name, language.tag))
        elif built_successfully is not None:
            build_failed.add((version.name, language.tag))

    logging.root.handlers[0].setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
    )

    build_sitemap(versions, languages, args.www_root, args.group)
    build_404(args.www_root, args.group)
    copy_robots_txt(
        args.www_root,
        args.group,
        args.skip_cache_invalidation,
        http,
    )
    make_symlinks(
        args.www_root,
        args.group,
        versions,
        languages,
        build_succeeded,
        args.skip_cache_invalidation,
        http,
    )
    proofread_canonicals(args.www_root, args.skip_cache_invalidation, http)

    logging.info("Full build done (%s).", format_seconds(perf_counter() - start_time))

    return len(build_failed) == 0


def parse_versions_from_devguide(http: urllib3.PoolManager) -> Versions:
    releases = http.request(
        "GET",
        "https://raw.githubusercontent.com/"
        "python/devguide/main/include/release-cycle.json",
        timeout=30,
    ).json()
    return Versions.from_json(releases)


def parse_languages_from_config() -> Languages:
    """Read config.toml to discover languages to build."""
    config = tomlkit.parse((HERE / "config.toml").read_text(encoding="UTF-8"))
    return Languages.from_json(config["defaults"], config["languages"])


def build_sitemap(versions: Versions, languages: Languages, www_root: Path, group):
    """Build a sitemap with all live versions and translations."""
    if not www_root.exists():
        logging.info("Skipping sitemap generation (www root does not even exist).")
        return
    logging.info("Starting sitemap generation...")
    template_path = HERE / "templates" / "sitemap.xml"
    template = jinja2.Template(template_path.read_text(encoding="UTF-8"))
    rendered_template = template.render(languages=languages, versions=versions)
    sitemap_path = www_root / "sitemap.xml"
    sitemap_path.write_text(rendered_template + "\n", encoding="UTF-8")
    sitemap_path.chmod(0o664)
    run(["chgrp", group, sitemap_path])


def build_404(www_root: Path, group):
    """Build a nice 404 error page to display in case PDFs are not built yet."""
    if not www_root.exists():
        logging.info("Skipping 404 page generation (www root does not even exist).")
        return
    logging.info("Copying 404 page...")
    not_found_file = www_root / "404.html"
    shutil.copyfile(HERE / "templates" / "404.html", not_found_file)
    not_found_file.chmod(0o664)
    run(["chgrp", group, not_found_file])


def copy_robots_txt(
    www_root: Path,
    group,
    skip_cache_invalidation,
    http: urllib3.PoolManager,
) -> None:
    """Copy robots.txt to www_root."""
    if not www_root.exists():
        logging.info("Skipping copying robots.txt (www root does not even exist).")
        return
    logging.info("Copying robots.txt...")
    template_path = HERE / "templates" / "robots.txt"
    robots_path = www_root / "robots.txt"
    shutil.copyfile(template_path, robots_path)
    robots_path.chmod(0o775)
    run(["chgrp", group, robots_path])
    if not skip_cache_invalidation:
        purge(http, "robots.txt")


def make_symlinks(
    www_root: Path,
    group: str,
    versions: Versions,
    languages: Languages,
    successful_builds: Set[tuple[str, str]],
    skip_cache_invalidation: bool,
    http: urllib3.PoolManager,
) -> None:
    """Maintains the /2/, /3/, and /dev/ symlinks for each language.

    Like:
    - /2/ → /2.7/
    - /3/ → /3.12/
    - /dev/ → /3.14/
    - /fr/3/ → /fr/3.12/
    - /es/dev/ → /es/3.14/
    """
    logging.info("Creating major and development version symlinks...")
    for symlink_name, symlink_target in (
        ("3", versions.current_stable.name),
        ("2", "2.7"),
        ("dev", versions.current_dev.name),
    ):
        for language in languages:
            if (symlink_target, language.tag) in successful_builds:
                symlink(
                    www_root,
                    language.tag,
                    symlink_target,
                    symlink_name,
                    group,
                    skip_cache_invalidation,
                    http,
                )


def symlink(
    www_root: Path,
    language_tag: str,
    directory: str,
    name: str,
    group: str,
    skip_cache_invalidation: bool,
    http: urllib3.PoolManager,
) -> None:
    """Used by major_symlinks and dev_symlink to maintain symlinks."""
    msg = "Creating symlink from /%s/ to /%s/"
    if language_tag == "en":  # English is rooted on /, no /en/
        path = www_root
        logging.debug(msg, name, directory)
    else:
        path = www_root / language_tag
        logging.debug(msg, f"{language_tag}/{name}", f"{language_tag}/{directory}")
    link = path / name
    directory_path = path / directory
    if not directory_path.exists():
        return  # No touching link, dest doc not built yet.

    if not link.exists() or os.readlink(link) != directory:
        # Link does not exist or points to the wrong target.
        link.unlink(missing_ok=True)
        link.symlink_to(directory)
        run(["chown", "-h", f":{group}", str(link)])
    if not skip_cache_invalidation:
        surrogate_key = f"{language_tag}/{name}"
        purge_surrogate_key(http, surrogate_key)


def proofread_canonicals(
    www_root: Path, skip_cache_invalidation: bool, http: urllib3.PoolManager
) -> None:
    """In www_root we check that all canonical links point to existing contents.

    It can happen that a canonical is "broken":

    - /3.11/whatsnew/3.11.html typically would link to
    /3/whatsnew/3.11.html, which may not exist yet.
    """
    logging.info("Checking canonical links...")
    worker_count = (os.cpu_count() or 1) + 2
    with concurrent.futures.ThreadPoolExecutor(worker_count) as executor:
        futures = {
            executor.submit(_check_canonical_rel, file, www_root)
            for file in www_root.glob("**/*.html")
        }
        paths_to_purge = {
            res.relative_to(www_root)  # strip the leading /srv/docs.python.org
            for fut in concurrent.futures.as_completed(futures)
            if (res := fut.result()) is not None
        }
    if not skip_cache_invalidation:
        purge(http, *paths_to_purge)


# Python 3.12 onwards doesn't use self-closing tags for <link rel="canonical">
_canonical_re = re.compile(
    b"""<link rel="canonical" href="https://docs.python.org/([^"]*)"(?: /)?>"""
)


def _check_canonical_rel(file: Path, www_root: Path):
    # Check for a canonical relation link in the HTML.
    # If one exists, ensure that the target exists
    # or otherwise remove the canonical link element.
    html = file.read_bytes()
    canonical = _canonical_re.search(html)
    if canonical is None:
        return None
    target = canonical[1].decode(encoding="UTF-8", errors="surrogateescape")
    if (www_root / target).exists():
        return None
    logging.info("Removing broken canonical from %s to %s", file, target)
    start, end = canonical.span()
    file.write_bytes(html[:start] + html[end:])
    return file


def purge(http: urllib3.PoolManager, *paths: Path | str) -> None:
    """Remove one or many paths from docs.python.org's CDN.

    To be used when a file changes, so the CDN fetches the new one.
    """
    base = "https://docs.python.org/"
    for path in paths:
        url = urljoin(base, str(path))
        logging.debug("Purging %s from CDN", url)
        http.request("PURGE", url, timeout=30)


def purge_surrogate_key(http: urllib3.PoolManager, surrogate_key: str) -> None:
    """Remove paths from docs.python.org's CDN.

    All paths matching the given 'Surrogate-Key' will be removed.
    This is set by the Nginx server for every language-version pair.
    To be used when a directory changes, so the CDN fetches the new one.

    https://www.fastly.com/documentation/reference/api/purging/#purge-tag
    """
    unset = "__UNSET__"
    service_id = os.environ.get("FASTLY_SERVICE_ID", unset)
    fastly_key = os.environ.get("FASTLY_TOKEN", unset)

    if service_id == unset or fastly_key == unset:
        logging.info("CDN secrets not set, skipping Surrogate-Key purge")
        return

    logging.info("Purging Surrogate-Key '%s' from CDN", surrogate_key)
    http.request(
        "POST",
        f"https://api.fastly.com/service/{service_id}/purge/{surrogate_key}",
        headers={"Fastly-Key": fastly_key},
        timeout=30,
    )


if __name__ == "__main__":
    sys.exit(main())
