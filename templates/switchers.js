(async function() {
  'use strict';

  if (!String.prototype.startsWith) {
    Object.defineProperty(String.prototype, 'startsWith', {
      value: function(search, rawPos) {
        const pos = rawPos > 0 ? rawPos|0 : 0;
        return this.substring(pos, pos + search.length) === search;
      }
    });
  }

  // Parses versions in URL segments like:
  // "3", "dev", "release/2.7" or "3.6rc2"
  const version_regexs = [
    '(?:\\d)',
    '(?:\\d\\.\\d[\\w\\d\\.]*)',
    '(?:dev)',
    '(?:release/\\d.\\d[\\x\\d\\.]*)'];

  // Fetch all available documentation versions from `release-cycle.json`
  const releaseCycleURL = "https://raw.githubusercontent.com/python/devguide/main/include/release-cycle.json";
  const releaseCycleResponse = await fetch(releaseCycleURL, { method: "GET"});
  if (!releaseCycleResponse.ok) {
    throw new Error("Error downloading release-cycle.json file.");
  }
  const releaseCycleData = await releaseCycleResponse.json();

  const all_versions = new Array();
  for (const version of releaseCycleData ) {
    all_versions.push(version);
  }
  // TODO: fetch available languges from an external JSON file
  const all_languages = $LANGUAGES;

  function quote_attr(str) {
      return '"' + str.replace('"', '\\"') + '"';
  }

  function build_version_select(release) {
    let buf = ['<select id="version_select">'];
    const major_minor = release.split(".").slice(0, 2).join(".");

    Object.entries(all_versions).forEach(function([version, title]) {
      if (version === major_minor) {
        buf.push('<option value=' + quote_attr(version) + ' selected="selected">' + release + '</option>');
      } else {
        buf.push('<option value=' + quote_attr(version) + '>' + title + '</option>');
      }
    });

    buf.push('</select>');
    return buf.join('');
  }

  function build_language_select(current_language) {
    let buf = ['<select id="language_select">'];

    Object.entries(all_languages).forEach(function([language, title]) {
      if (language === current_language) {
        buf.push('<option value="' + language + '" selected="selected">' + title + '</option>');
      } else {
        buf.push('<option value="' + language + '">' + title + '</option>');
      }
    });
    if (!(current_language in all_languages)) {
        // In case we're browsing a language that is not yet in all_languages.
        buf.push('<option value="' + current_language + '" selected="selected">' +
                 current_language + '</option>');
        all_languages[current_language] = current_language;
    }
    buf.push('</select>');
    return buf.join('');
  }

  function navigate_to_first_existing(urls) {
    // Navigate to the first existing URL in urls.
    const url = urls.shift();
    if (urls.length == 0 || url.startsWith("file:///")) {
      window.location.href = url;
      return;
    }
    fetch(url)
      .then(function(response) {
        if (response.ok) {
          window.location.href = url;
        } else {
          navigate_to_first_existing(urls);
        }
      })
      .catch(function(error) {
        navigate_to_first_existing(urls);
      });
  }

  function on_version_switch() {
    const selected_version = this.options[this.selectedIndex].value + '/';
    const url = window.location.href;
    const current_language = language_segment_from_url();
    const current_version = version_segment_from_url();
    const new_url = url.replace('/' + current_language + current_version,
                                '/' + current_language + selected_version);
    if (new_url != url) {
      navigate_to_first_existing([
        new_url,
        url.replace('/' + current_language + current_version,
                    '/' + selected_version),
        '/' + current_language + selected_version,
        '/' + selected_version,
        '/'
      ]);
    }
  }

  function on_language_switch() {
    let selected_language = this.options[this.selectedIndex].value + '/';
    const url = window.location.href;
    const current_language = language_segment_from_url();
    const current_version = version_segment_from_url();
    if (selected_language == 'en/') // Special 'default' case for English.
      selected_language = '';
    let new_url = url.replace('/' + current_language + current_version,
                              '/' + selected_language + current_version);
    if (new_url != url) {
      navigate_to_first_existing([
        new_url,
        '/'
      ]);
    }
  }

  // Returns the path segment of the language as a string, like 'fr/'
  // or '' if not found.
  function language_segment_from_url() {
    const path = window.location.pathname;
    const language_regexp = '/((?:' + Object.keys(all_languages).join("|") + ')/)'
    const match = path.match(language_regexp);
    if (match !== null)
      return match[1];
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
    if (match !== null)
      return match[1];
    return ''
  }

  function create_placeholders_if_missing() {
    const version_segment = version_segment_from_url();
    const language_segment = language_segment_from_url();
    const index = "/" + language_segment + version_segment;

    if (document.querySelectorAll('.version_switcher_placeholder').length > 0) {
      return;
    }

    const html = '<span class="language_switcher_placeholder"></span> \
<span class="version_switcher_placeholder"></span> \
<a href="/" id="indexlink">Documentation</a> &#187;';

    const probable_places = [
      "body>div.related>ul>li:not(.right):contains('Documentation'):first",
      "body>div.related>ul>li:not(.right):contains('documentation'):first",
    ];

    for (let i = 0; i < probable_places.length; i++) {
      let probable_place = $(probable_places[i]);
      if (probable_place.length == 1) {
        probable_place.html(html);
        document.getElementById('indexlink').href = index;
        return;
      }
    }
  }

  document.addEventListener('DOMContentLoaded', function() {
    const language_segment = language_segment_from_url();
    const current_language = language_segment.replace(/\/+$/g, '') || 'en';
    const version_select = build_version_select(DOCUMENTATION_OPTIONS.VERSION);

    create_placeholders_if_missing();

    let placeholders = document.querySelectorAll('.version_switcher_placeholder');
    placeholders.forEach(function(placeholder) {
      placeholder.innerHTML = version_select;

      let selectElement = placeholder.querySelector('select');
      selectElement.addEventListener('change', on_version_switch);
    });

    const language_select = build_language_select(current_language);

    placeholders = document.querySelectorAll('.language_switcher_placeholder');
    placeholders.forEach(function(placeholder) {
      placeholder.innerHTML = language_select;

      let selectElement = placeholder.querySelector('select');
      selectElement.addEventListener('change', on_language_switch);
    });
  });
})();
