'use strict';

const _CURRENT_RELEASE = DOCUMENTATION_OPTIONS.VERSION || '';
const _CURRENT_VERSION = _CURRENT_RELEASE.split('.', 2).join('.');
const _CURRENT_LANGUAGE = DOCUMENTATION_OPTIONS.LANGUAGE?.toLowerCase() || 'en';
const _CURRENT_PREFIX = (() => {
  // Sphinx 7.2+ defines the content root data attribute in the HTML element.
  const _CONTENT_ROOT = document.documentElement.dataset.content_root;
  if (_CONTENT_ROOT !== undefined) {
    return new URL(_CONTENT_ROOT, window.location).pathname;
  }
  // Fallback for older versions of Sphinx (used in Python 3.10 and older).
  const _NUM_PREFIX_PARTS = _CURRENT_LANGUAGE === 'en' ? 2 : 3;
  return window.location.pathname.split('/', _NUM_PREFIX_PARTS).join('/') + '/';
})();

const all_versions = $VERSIONS;
const all_languages = $LANGUAGES;

const _create_version_select = () => {
  const select = document.createElement('select');
  select.className = 'version-select';

  for (const [version, title] of Object.entries(all_versions)) {
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

const _create_language_select = (current_language) => {
  if (!(current_language in all_languages)) {
    // In case we are browsing a language that is not yet in all_languages.
    all_languages[current_language] = current_language;
  }

  const select = document.createElement('select');
  select.className = 'language-select';

  for (const [language, title] of Object.entries(all_languages)) {
    const option = document.createElement('option');
    option.value = language;
    option.text = title;
    if (language === current_language) option.selected = true;
    select.add(option);
  }

  return select;
};

const _navigate_to_first_existing = (urls) => {
  // Navigate to the first existing URL in urls.
  for (const url of urls) {
    if (url.startsWith('file:///')) {
      window.location.href = url;
      return;
    }
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

const _on_version_switch = (event) => {
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

const _on_language_switch = (event) => {
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

const _initialise_switchers = () => {
  const version_select = _create_version_select();
  document
    .querySelectorAll('.version_switcher_placeholder')
    .forEach((placeholder) => {
      const s = version_select.cloneNode(true);
      s.addEventListener('change', _on_version_switch);
      placeholder.append(s);
    });

  const language_select = _create_language_select(_CURRENT_LANGUAGE);
  document
    .querySelectorAll('.language_switcher_placeholder')
    .forEach((placeholder) => {
      const s = language_select.cloneNode(true);
      s.addEventListener('change', _on_language_switch);
      placeholder.append(s);
    });
};

if (document.readyState !== 'loading') {
  _initialise_switchers();
} else {
  document.addEventListener('DOMContentLoaded', _initialise_switchers);
}
