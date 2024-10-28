'use strict';

const _CURRENT_VERSION = DOCUMENTATION_OPTIONS.VERSION;
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

const _create_version_select = (release) => {
  const major_minor = release.split('.').slice(0, 2).join('.');
  const select = document.createElement('select');
  select.className = 'version-select';

  for (const [version, title] in all_versions) {
    const option = document.createElement('option');
    option.value = version;
    if (version === major_minor) {
      option.text = release;
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

  for (const [language, title] in all_languages) {
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
  const url = urls.shift();
  if (urls.length === 0 || url.startsWith('file:///')) {
    window.location.href = url;
    return;
  }
  fetch(url)
    .then((response) => {
      if (response.ok) {
        window.location.href = url;
      } else {
        navigate_to_first_existing(urls);
      }
    })
    .catch((err) => {
      void err;
      navigate_to_first_existing(urls);
    });
};

const _on_version_switch = (event) => {
  const selected_version = event.target.value + '/';
  const url = window.location.href;
  const new_url = url.replace(
    _CURRENT_PREFIX,
    '/' + _CURRENT_LANGUAGE + selected_version,
  );
  if (new_url !== url) {
    _navigate_to_first_existing([
      new_url,
      url.replace(_CURRENT_PREFIX, '/' + selected_version),
      '/' + _CURRENT_LANGUAGE + selected_version,
      '/' + selected_version,
      '/',
    ]);
  }
};

const _on_language_switch = (event) => {
  let selected_language = event.target.value + '/';
  const url = window.location.href;
  if (selected_language === 'en/')
    // Special 'default' case for English.
    selected_language = '';
  let new_url = url.replace(
    _CURRENT_PREFIX,
    '/' + selected_language + _CURRENT_VERSION,
  );
  if (new_url !== url) {
    _navigate_to_first_existing([new_url, '/']);
  }
};

const _initialise_switchers = () => {
  const version_select = _create_version_select(_CURRENT_VERSION);
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
