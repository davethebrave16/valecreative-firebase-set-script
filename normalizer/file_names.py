"""Faithful port of valecreative-admin-backoffice/src/utils/imageFileName.ts, plus Storage URL helpers.

  cover image   → {slug}.{ext}
  gallery image → {slug}-{n}.{ext}   (n = position in the gallery, 1-based)
Extension lowercased; base name never contains spaces or accents; file stays in its uuid folder.
"""
from __future__ import annotations

import re
from urllib.parse import quote, unquote

from .slug_rules import to_slug

_EXT = re.compile(r'\.([A-Za-z0-9]+)$')
_STRIP_EXT = re.compile(r'\.[^.]+$')


def file_extension(file_name: str) -> str:
    m = _EXT.search(file_name)
    return m.group(1).lower() if m else 'jpg'


def _base_from_original(file_name: str) -> str:
    return to_slug(_STRIP_EXT.sub('', file_name, count=1)) or 'image'


def cover_file_name(slug: str | None, file_name: str) -> str:
    base = to_slug(slug or '') or _base_from_original(file_name)
    return f'{base}.{file_extension(file_name)}'


def gallery_file_name(slug: str | None, n: int, file_name: str) -> str:
    """Like galleryFileName() in TS without the collision suffix: the script assigns 1..k for a whole
    gallery at once, so the names are distinct by construction."""
    base = to_slug(slug or '') or _base_from_original(file_name)
    return f'{base}-{n}.{file_extension(file_name)}'


def path_from_url(download_url: str | None) -> str | None:
    """Storage object path from a Firebase download URL (…/o/artworks%2Fuuid%2Fname.jpg?alt=media…)."""
    if not download_url:
        return None
    m = re.search(r'/o/([^?#]+)', download_url)
    return unquote(m.group(1)) if m else None


def build_download_url(bucket: str, path: str, token: str) -> str:
    return f'https://firebasestorage.googleapis.com/v0/b/{bucket}/o/{quote(path, safe="")}?alt=media&token={token}'


def folder_of(path: str) -> str:
    return path.rsplit('/', 1)[0]


def name_of(path: str) -> str:
    return path.rsplit('/', 1)[-1]


def stem_of(file_name: str) -> str:
    return _STRIP_EXT.sub('', file_name, count=1)
