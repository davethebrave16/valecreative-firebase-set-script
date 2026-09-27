"""Faithful port of valecreative-admin-backoffice/src/utils/slugify.ts.

Keep in sync with the TypeScript version: tests/test_rules.py compares both outputs
(tests/fixtures/ts_expected.json is generated from the TS code by tests/generate_ts_fixtures.sh).
"""
from __future__ import annotations

import re
import unicodedata

# export const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/
SLUG_PATTERN = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
# export const CONTENT_SLUG_PATTERN = /^[a-z0-9]+(?:[-_][a-z0-9]+)*$/
CONTENT_SLUG_PATTERN = re.compile(r'^[a-z0-9]+(?:[-_][a-z0-9]+)*$')

_DIACRITICS = re.compile('[̀-ͯ]')
_APOSTROPHES = re.compile("['’‘`]")
_NON_ALNUM = re.compile(r'[^a-z0-9]+')
_EDGE_HYPHENS = re.compile(r'^-+|-+$')


def to_slug(value: str) -> str:
    """str.normalize('NFD') → strip diacritics → toLowerCase → drop apostrophes → non-alnum runs → '-' → trim '-'."""
    s = unicodedata.normalize('NFD', value)
    s = _DIACRITICS.sub('', s)
    s = s.lower()
    s = _APOSTROPHES.sub('', s)
    s = _NON_ALNUM.sub('-', s)
    return _EDGE_HYPHENS.sub('', s)


def slug_pattern_for(collection: str) -> re.Pattern[str]:
    # contents slugs are lookup keys (e.g. homepage_hero), so underscores are allowed.
    return CONTENT_SLUG_PATTERN if collection == 'contents' else SLUG_PATTERN
