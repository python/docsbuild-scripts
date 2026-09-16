#!/usr/bin/env python

import argparse
import concurrent.futures
import logging
import re
from pathlib import Path

import git
import urllib3
from tabulate import tabulate

import build_docs

logger = logging.getLogger(__name__)
MAX_WORKERS = 16
http = urllib3.PoolManager(maxsize=MAX_WORKERS)
VERSIONS = build_docs.parse_versions_from_peps_site(http)
LANGUAGES = build_docs.parse_languages_from_config()


def parse_args():
    parser = argparse.ArgumentParser(
        description="""Check the version of our build in different branches
        Hint: Use with | column -t"""
    )
    parser.add_argument("cpython_clone", help="Path to a clone of CPython", type=Path)
    return parser.parse_args()


def find_upstream_remote_name(repo: git.Repo) -> str:
    """Find a remote in the repo that matches the URL pattern."""
    for remote in repo.remotes:
        for url in remote.urls:
            if "github.com/python" in url:
                return f"{remote.name}/"


def find_sphinx_spec(text: str) -> str:
    if found := re.search(
        """sphinx[=<>~]{1,2}[0-9.]{3,}|needs_sphinx = [0-9.'"]*""",
        text,
        flags=re.IGNORECASE,
    ):
        return found.group(0).replace(" ", "").replace('"', "'")
    return "ø"


def find_sphinx_in_files(repo: git.Repo, branch_or_tag, filenames):
    upstream = find_upstream_remote_name(repo)
    # Just in case you don't use upstream/:
    branch_or_tag = branch_or_tag.replace("upstream/", upstream)
    specs = []
    for filename in filenames:
        try:
            blob = repo.git.show(f"{branch_or_tag}:{filename}")
        except git.exc.GitCommandError:
            specs.append("ø")
        else:
            specs.append(find_sphinx_spec(blob))
    return specs


CONF_FILES = {
    "requirements.txt": "Doc/requirements.txt",
    "conf.py": "Doc/conf.py",
}


def branch_or_tag_for(version):
    if version.status == "EOL":
        return f"tags/{version.branch_or_tag}"
    return f"upstream/{version.branch_or_tag}"


def search_sphinx_versions_in_cpython(repo: git.Repo):
    repo.git.fetch("https://github.com/python/cpython")
    filenames = CONF_FILES.values()
    table = [
        [
            version.name,
            *find_sphinx_in_files(repo, branch_or_tag_for(version), filenames),
        ]
        for version in VERSIONS
    ]
    headers = ["version", *CONF_FILES.keys()]
    print(tabulate(table, headers=headers, tablefmt="github", disable_numparse=True))


def get_version_in_prod(language: str, version: str) -> str:
    if language == "en":
        url = f"https://docs.python.org/{version}/"
    else:
        url = f"https://docs.python.org/{language}/{version}/"
    try:
        response = http.request("GET", url, timeout=5)
    except urllib3.exceptions.HTTPError:
        return "(timeout)"
    # Python 2.6--3.7: sphinx.pocoo.org
    # from Python 3.8: www.sphinx-doc.org
    if created_using := re.search(
        r"(?:sphinx.pocoo.org|www.sphinx-doc.org).*?([0-9.]+[0-9])",
        response.data.decode("utf-8", errors="replace"),
    ):
        return created_using.group(1)
    return "ø"


def which_sphinx_is_used_in_production():
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            (version.name, language.tag): executor.submit(
                get_version_in_prod, language.tag, version.name
            )
            for version in VERSIONS
            for language in LANGUAGES
        }
    table = [
        [
            version.name,
            *[futures[version.name, language.tag].result() for language in LANGUAGES],
        ]
        for version in VERSIONS
    ]
    headers = ["version", *[language.tag for language in LANGUAGES]]
    print(tabulate(table, headers=headers, tablefmt="github", disable_numparse=True))


def check_versions(cpython_clone: str) -> None:
    logging.basicConfig(level=logging.INFO)
    repo = git.Repo(cpython_clone)
    print("Sphinx configuration in various branches:", end="\n\n")
    search_sphinx_versions_in_cpython(repo)
    print()
    print("Sphinx build as seen on docs.python.org:", end="\n\n")
    which_sphinx_is_used_in_production()


def main():
    args = parse_args()
    check_versions(args.cpython_clone)


if __name__ == "__main__":
    main()
