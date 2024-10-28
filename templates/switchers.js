'use strict';

// File URIs must begin with either one or three forward slashes
const _is_file_uri = (uri) => uri.startsWith('file:/');

const _IS_LOCAL = _is_file_uri(window.location.href);
const _CURRENT_RELEASE = DOCUMENTATION_OPTIONS.VERSION || '';
const _CURRENT_VERSION = _CURRENT_RELEASE.split('.', 2).join('.');
const _CURRENT_LANGUAGE = DOCUMENTATION_OPTIONS.LANGUAGE?.toLowerCase() || 'en';
const _CURRENT_PREFIX = (() => {
  if (_IS_LOCAL) return null;
  // Sphinx 7.2+ defines the content root data attribute in the HTML element.
  const _CONTENT_ROOT = document.documentElement.dataset.content_root;
  if (_CONTENT_ROOT !== undefined) {
    return new URL(_CONTENT_ROOT, window.location).pathname;
  }
  // Fallback for older versions of Sphinx (used in Python 3.10 and older).
  const _NUM_PREFIX_PARTS = _CURRENT_LANGUAGE === 'en' ? 2 : 3;
  return window.location.pathname.split('/', _NUM_PREFIX_PARTS).join('/') + '/';
})();

const _ALL_VERSIONS = new Map(Object.entries($VERSIONS));
const _ALL_LANGUAGES = new Map(Object.entries($LANGUAGES));

/**
 * @param {Map<string, string>} versions
 * @returns {HTMLSelectElement}
 * @private
 */
const _create_version_select = (versions) => {
  const select = document.createElement('select');
  select.className = 'version-select';
  if (_IS_LOCAL) {
    select.disabled = true;
    select.title = 'Version switching is disabled in local builds';
  }

  for (const [version, title] of versions) {
    const option = document.createElement('option');
    option.value = version;
    if (version === _CURRENT_VERSION) {
      option.text = _CURRENT_RELEASE;
      option.selected = true;
    } else {
      option.text = title;
    }
    select.add(option);
  }

  return select;
};

/**
 * @param {Map<string, string>} languages
 * @returns {HTMLSelectElement}
 * @private
 */
const _create_language_select = (languages) => {
  if (!languages.has(_CURRENT_LANGUAGE)) {
    // In case we are browsing a language that is not yet in languages.
    languages.set(_CURRENT_LANGUAGE, _CURRENT_LANGUAGE);
  }

  const select = document.createElement('select');
  select.className = 'language-select';
  if (_IS_LOCAL) {
    select.disabled = true;
    select.title = 'Language switching is disabled in local builds';
  }

  for (const [language, title] of languages) {
    const option = document.createElement('option');
    option.value = language;
    option.text = title;
    if (language === _CURRENT_LANGUAGE) option.selected = true;
    select.add(option);
  }

  return select;
};

/**
 * Change the current page to the first existing URL in the list.
 * @param {Array<string>} urls
 * @private
 */
const _navigate_to_first_existing = (urls) => {
  // Navigate to the first existing URL in urls.
  for (const url of urls) {
    fetch(url)
      .then((response) => {
        if (response.ok) {
          window.location.href = url;
          return url;
        }
      })
      .catch((err) => {
        console.error(`Error when fetching '${url}'!`);
        console.error(err);
      });
  }

  // if all else fails, redirect to the d.p.o root
  window.location.href = '/';
  return '/';
};

/**
 * Callback for the version switcher.
 * @param {Event} event
 * @returns {void}
 * @private
 */
const _on_version_switch = (event) => {
  if (_IS_LOCAL) return;

  const selected_version = event.target.value;
  // English has no language prefix.
  const new_prefix_en = `/${selected_version}/`;
  const new_prefix =
    _CURRENT_LANGUAGE === 'en'
      ? new_prefix_en
      : `/${_CURRENT_LANGUAGE}/${selected_version}/`;
  if (_CURRENT_PREFIX !== new_prefix) {
    // Try the following pages in order:
    // 1. The current page in the current language with the new version
    // 2. The current page in English with the new version
    // 3. The documentation home in the current language with the new version
    // 4. The documentation home in English with the new version
    _navigate_to_first_existing([
      window.location.href.replace(_CURRENT_PREFIX, new_prefix),
      window.location.href.replace(_CURRENT_PREFIX, new_prefix_en),
      new_prefix,
      new_prefix_en,
    ]);
  }
};

/**
 * Callback for the language switcher.
 * @param {Event} event
 * @returns {void}
 * @private
 */
const _on_language_switch = (event) => {
  if (_IS_LOCAL) return;

  const selected_language = event.target.value;
  // English has no language prefix.
  const new_prefix =
    selected_language === 'en'
      ? `/${_CURRENT_VERSION}/`
      : `/${selected_language}/${_CURRENT_VERSION}/`;
  if (_CURRENT_PREFIX !== new_prefix) {
    // Try the following pages in order:
    // 1. The current page in the new language with the current version
    // 2. The documentation home in the new language with the current version
    _navigate_to_first_existing([
      window.location.href.replace(_CURRENT_PREFIX, new_prefix),
      new_prefix,
    ]);
  }
};

/**
 * Initialisation function for the version and language switchers.
 * @returns {void}
 * @private
 */
const _initialise_switchers = () => {
  const versions = _ALL_VERSIONS;
  const languages = _ALL_LANGUAGES;

  const version_select = _create_version_select(versions);
  document
    .querySelectorAll('.version_switcher_placeholder')
    .forEach((placeholder) => {
      const s = version_select.cloneNode(true);
      s.addEventListener('change', _on_version_switch);
      placeholder.append(s);
      placeholder.classList.remove('version_switcher_placeholder');
    });

  const language_select = _create_language_select(languages);
  document
    .querySelectorAll('.language_switcher_placeholder')
    .forEach((placeholder) => {
      const s = language_select.cloneNode(true);
      s.addEventListener('change', _on_language_switch);
      placeholder.append(s);
      placeholder.classList.remove('language_switcher_placeholder');
    });
};

if (document.readyState !== 'loading') {
  _initialise_switchers();
} else {
  document.addEventListener('DOMContentLoaded', _initialise_switchers);
}
