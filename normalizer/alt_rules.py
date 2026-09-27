"""Default alt text, same policy as the backoffice: alt = record title unless an admin wrote a real description.

An alt is considered a leftover file name (and replaced) when it is empty, equals the file name/stem,
equals the name of any other file in the bucket (e.g. a replaced TIFF), equals the slug, starts with a
camera/app prefix, is only digits/underscores/separators, is snake_case without spaces, or contains an
image extension. Anything else is treated as descriptive and left untouched.
"""
from __future__ import annotations

import re

from .file_names import stem_of
from .slug_rules import to_slug

_CAMERA_PREFIX = re.compile(r'^\d*[-_ ]?(img|dsc|dscn|dcim|pxl|whatsapp|screenshot|image|photo|foto)([-_ .]|\d|$)', re.I)
_ONLY_NUMBERS = re.compile(r'^[\d_\-\s().n]+$', re.I)  # e.g. 495658824_1234_n, 100_7158, "(1)"
_HAS_EXTENSION = re.compile(r'\.(jpe?g|png|gif|webp|avif|heic|tiff?|bmp)\b', re.I)
_FILE_TOKENS = re.compile(r'(^|[-_ ])(jpe?g|png|tiff?)([-_ ]|$)', re.I)  # e.g. "pitture-jpg_10 (1)"
_SNAKE_CASE = re.compile(r'^\S*_\S*$')  # e.g. "bicchere_personalizzato" (no spaces, has underscores)


def looks_like_filename(alt: str | None, file_name: str, slug: str | None = None,
                        known_file_stems: frozenset = frozenset()) -> bool:
    """known_file_stems: to_slug() of every file stem in the bucket, to catch alts copied from another file."""
    a = (alt or '').strip()
    if not a:
        return True
    stem = stem_of(file_name)
    if a == file_name or a == stem or to_slug(a) == to_slug(stem):
        return True
    if slug and (a == slug or to_slug(a) == slug and '-' in a and ' ' not in a):
        return True
    if to_slug(a) in known_file_stems:
        return True
    if (_CAMERA_PREFIX.match(a) or _ONLY_NUMBERS.match(a) or _HAS_EXTENSION.search(a) or _FILE_TOKENS.search(a)
            or _SNAKE_CASE.match(a)):
        return True
    return False
