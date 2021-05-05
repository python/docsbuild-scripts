(function() {
  'use strict';

  if (!String.prototype.startsWith) {
    Object.defineProperty(String.prototype, 'startsWith', {
      value: function(search, rawPos) {
        var pos = rawPos > 0 ? rawPos|0 : 0;
        return this.substring(pos, pos + search.length) === search;
      }
    });
  }

  // Parses versions in URL segments like:
  // "3", "dev", "release/2.7" or "3.6rc2"
  var version_regexs = [
    '(?:\\d)',
    '(?:\\d\\.\\d[\\w\\d\\.]*)',
    '(?:dev)',
    '(?:release/\\d.\\d[\\x\\d\\.]*)'];

  var all_versions = $VERSIONS;
  var all_languages = $LANGUAGES;

  function quote_attr(str) {
      return '"' + str.replace('"', '\\"') + '"';
  }

  function build_version_select(release) {
    var buf = ['<select id="version_select">'];
    var major_minor = release.split(".").slice(0, 2).join(".");

    $.each(all_versions, function(version, title) {
      if (version == major_minor)
        buf.push('<option value=' + quote_attr(version) + ' selected="selected">' + release + '</option>');
      else
        buf.push('<option value=' + quote_attr(version) + '>' + title + '</option>');
    });

    buf.push('</select>');
    return buf.join('');
  }

  function build_language_select(current_language) {
    var buf = ['<select id="language_select">'];

    $.each(all_languages, function(language, title) {
      if (language == current_language)
        buf.push('<option value="' + language + '" selected="selected">' +
                 all_languages[current_language] + '</option>');
      else
        buf.push('<option value="' + language + '">' + title + '</option>');
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
    var url = urls.shift();
    if (urls.length == 0 || url.startsWith("file:///")) {
      window.location.href = url;
      return;
    }
    $.ajax({
      url: url,
      success: function() {
        window.location.href = url;
      },
      error: function() {
        navigate_to_first_existing(urls);
      }
    });
  }

  function on_version_switch() {
    var selected_version = $(this).children('option:selected').attr('value') + '/';
    var url = window.location.href;
    var current_language = language_segment_from_url();
    var current_version = version_segment_from_url();
    var new_url = url.replace('/' + current_language + current_version,
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
    var selected_language = $(this).children('option:selected').attr('value') + '/';
    var url = window.location.href;
    var current_language = language_segment_from_url();
    var current_version = version_segment_from_url();
    if (selected_language == 'en/') // Special 'default' case for english.
      selected_language = '';
    var new_url = url.replace('/' + current_language + current_version,
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
    var path = window.location.pathname;
    var language_regexp = '/((?:' + Object.keys(all_languages).join("|") + ')/)'
    var match = path.match(language_regexp);
    if (match !== null)
      return match[1];
    return '';
  }

  // Returns the path segment of the version as a string, like '3.6/'
  // or '' if not found.
  function version_segment_from_url() {
    var path = window.location.pathname;
    var language_segment = language_segment_from_url();
    var version_segment = '(?:(?:' + version_regexs.join('|') + ')/)';
    var version_regexp = language_segment + '(' + version_segment + ')';
    var match = path.match(version_regexp);
    if (match !== null)
      return match[1];
    return ''
  }

  function create_placeholders_if_missing() {
    var version_segment = version_segment_from_url();
    var language_segment = language_segment_from_url();
    var index = "/" + language_segment + version_segment;

    if ($('.version_switcher_placeholder').length)
      return;

    var html = '<span class="language_switcher_placeholder"></span> \
<span class="version_switcher_placeholder"></span> \
<a href="/" id="indexlink">Documentation</a> &#187;';

    var probable_places = [
      "body>div.related>ul>li:not(.right):contains('Documentation'):first",
      "body>div.related>ul>li:not(.right):contains('documentation'):first",
    ];

    for (var i = 0; i < probable_places.length; i++) {
      var probable_place = $(probable_places[i]);
      if (probable_place.length == 1) {
        probable_place.html(html);
        document.getElementById('indexlink').href = index;
        return;
      }
    }
  }

  $(document).ready(function() {
    var language_segment = language_segment_from_url();
    var current_language = language_segment.replace(/\/+$/g, '') || 'en';
    var version_select = build_version_select(DOCUMENTATION_OPTIONS.VERSION);

    create_placeholders_if_missing();
    $('.version_switcher_placeholder').html(version_select);
    $('.version_switcher_placeholder select').bind('change', on_version_switch);

    var language_select = build_language_select(current_language);

    $('.language_switcher_placeholder').html(language_select);
    $('.language_switcher_placeholder select').bind('change', on_language_switch);
  });
})();
