"""Literal notification text with conservative, user-activated web links."""
from html import escape
import re
from urllib.parse import urlsplit
import unicodedata

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextOption
from PyQt6.QtWidgets import QTextBrowser

# Newlines delimit separate prose. Other controls remain in the candidate so
# validation cannot turn the prefix of a malformed URL into a clickable link.
URL = re.compile(r'https?://[^\n <>\"\']+', re.IGNORECASE)
ENCODED_CONTROL = re.compile(r'%(?:0[0-9a-f]|1[0-9a-f]|7f)', re.IGNORECASE)


def valid_http_url(value):
    if not isinstance(value, str) or not value or '...' in value or '…' in value:
        return False
    if any(char.isspace() or unicodedata.category(char).startswith('C') for char in value):
        return False
    if '\\' in value or ENCODED_CONTROL.search(value):
        return False
    try:
        parsed = urlsplit(value)
        return _valid_parts(parsed)
    except (ValueError, UnicodeError):
        return False


def _valid_parts(parsed):
    # Accessing port validates its range and rejects malformed host:port input.
    parsed.port
    return (parsed.scheme.lower() in {'http', 'https'} and bool(parsed.hostname)
            and parsed.username is None and parsed.password is None)


def _prose_suffix(value):
    suffix = ''
    while value:
        last = value[-1]
        if last in '.,;!:' or (last in ')]}' and value.count(last) > value.count({')': '(', ']': '[', '}': '{'}[last])):
            suffix, value = last + suffix, value[:-1]
        else:
            break
    return value, suffix


def linked_text(value):
    """Escape everything and link only complete HTTP(S) URL text.

    Markdown and Label (URL) punctuation remains literal, retaining all source
    text while making the contained URL usable. No provider markup is executed.
    """
    value = str(value or '')
    pieces, offset = [], 0
    for match in URL.finditer(value):
        pieces.append(escape(value[offset:match.start()]))
        candidate, suffix = _prose_suffix(match.group())
        # An ellipsis is evidence of incomplete text; never trim it into a link.
        if valid_http_url(match.group()) and valid_http_url(candidate):
            pieces.append('<a href="' + escape(candidate, quote=True) + '">' + escape(candidate) + '</a>' + escape(suffix))
        else:
            pieces.append(escape(match.group()))
        offset = match.end()
    pieces.append(escape(value[offset:]))
    return ''.join(pieces).replace('\n', '<br>')


def notification_html(item, stamp, colors):
    document = ('<h2 style="margin:0 0 4px 0;font-size:21px;color:' + colors['text'] + ';">'
                + escape(str(item.get('title') or '(Untitled)')).replace('\n', '<br>') + '</h2>'
                + '<p style="color:' + colors['muted'] + ';margin:0 0 12px 0;font-size:13px;">'
                + escape(stamp(item.get('created_at'))) + '</p>'
                + '<p style="line-height:1.55;font-size:14px;margin-top:10px;white-space:pre-wrap;color:' + colors['text'] + ';">'
                + linked_text(item.get('body')) + '</p>')
    if item.get('url'):
        url = str(item['url'])
        # Top-level URL fields are complete values, not prose to be repaired.
        content = ('<a href="' + escape(url, quote=True) + '">' + escape(url) + '</a>') if valid_http_url(url) else escape(url)
        document += '<p style="margin-top:10px;">' + content.replace('\n', '<br>') + '</p>'
    if item.get('type') in {'mirror', 'notification', 'mirrored_notification'}:
        document += '<p style="color:' + colors['muted'] + ';font-size:12px;">Shown as provided by the phone. The source app may shorten notification text.</p>'
    return document


class NotificationBrowser(QTextBrowser):
    """Never load provider resources or navigate automatically."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)

    def loadResource(self, resource_type, name):
        return None
