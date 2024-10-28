'use strict';

// Parses versions in URL segments like:
// "3", "dev", "release/2.7" or "3.6rc2"
const version_regexs = [
  '(?:\\d)',
  '(?:\\d\\.\\d[\\w\\d\\.]*)',
  '(?:dev)',
  '(?:release/\\d.\\d[\\x\\d\\.]*)',
];

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

const _on_version_switch = () => {
  const selected_version = this.options[this.selectedIndex].value + '/';
  const url = window.location.href;
  const current_language = language_segment_from_url();
  const current_version = version_segment_from_url();
  const new_url = url.replace(
    '/' + current_language + current_version,
    '/' + current_language + selected_version,
  );
  if (new_url !== url) {
    _navigate_to_first_existing([
      new_url,
      url.replace(
        '/' + current_language + current_version,
        '/' + selected_version,
      ),
      '/' + current_language + selected_version,
      '/' + selected_version,
      '/',
    ]);
  }
};

const _on_language_switch = () => {
  let selected_language = this.options[this.selectedIndex].value + '/';
  const url = window.location.href;
  const current_language = language_segment_from_url();
  const current_version = version_segment_from_url();
  if (selected_language === 'en/')
    // Special 'default' case for English.
    selected_language = '';
  let new_url = url.replace(
    '/' + current_language + current_version,
    '/' + selected_language + current_version,
  );
  if (new_url !== url) {
    _navigate_to_first_existing([new_url, '/']);
  }
};

// Returns the path segment of the language as a string, like 'fr/'
// or '' if not found.
function language_segment_from_url() {
  const path = window.location.pathname;
  const language_regexp =
    '/((?:' + Object.keys(all_languages).join('|') + ')/)';
  const match = path.match(language_regexp);
  if (match !== null) return match[1];
  return '';
}

// Returns the path segment of the version as a string, like '3.6/'
// or '' if not found.
function version_segment_from_url() {
  const path = window.location.pathname;
  const language_segment = language_segment_from_url();
  const version_segment = '(?:(?:' + version_regexs.join('|') + ')/)';
  const version_regexp = language_segment + '(' + version_segment + ')';
  const match = path.match(version_regexp);
  if (match !== null) return match[1];
  return '';
}
const _initialise_switchers = () => {
  const language_segment = language_segment_from_url();
  const current_language = language_segment.replace(/\/+$/g, '') || 'en';

  const version_select = _create_version_select(DOCUMENTATION_OPTIONS.VERSION);
  document
    .querySelectorAll('.version_switcher_placeholder')
    .forEach((placeholder) => {
      const s = version_select.cloneNode(true);
      s.addEventListener('change', _on_version_switch);
      placeholder.append(s);
    });

  const language_select = _create_language_select(current_language);
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
