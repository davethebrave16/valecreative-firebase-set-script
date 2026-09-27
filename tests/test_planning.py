"""Slug/file planning on fake documents (no Firebase access)."""
from __future__ import annotations

import datetime as dt
import unittest

from normalize_data import build_plan, plan_slugs


class FakeRef:
    def __init__(self, path):
        self.path = path


class FakeDoc:
    def __init__(self, collection, doc_id, data, day=1):
        self.id = doc_id
        self.reference = FakeRef(f'{collection}/{doc_id}')
        self._data = {'createdAt': dt.datetime(2026, 7, day, tzinfo=dt.timezone.utc), **data}

    def to_dict(self):
        return dict(self._data)


class FakeBlob:
    def __init__(self, name, cache_control=None):
        self.name, self.cache_control, self.size = name, cache_control, 100


def url(path):
    from urllib.parse import quote
    return f'https://firebasestorage.googleapis.com/v0/b/b/o/{quote(path, safe="")}?alt=media&token=t'


class SlugPlanning(unittest.TestCase):
    def test_non_conforming_and_duplicates(self):
        docs = [
            FakeDoc('artworks', 'a1', {'slug': 'albero', 'title': 'Albero'}, day=4),
            FakeDoc('artworks', 'a2', {'slug': 'albero', 'title': 'Albero'}, day=8),
            FakeDoc('artworks', 'a3', {'slug': 'Padre e figlio', 'title': 'Padre e figlio'}),
            FakeDoc('artworks', 'a4', {'slug': 'diana---sottobosco', 'title': 'Diana - sottobosco'}),
            FakeDoc('artworks', 'a5', {'slug': 'sovrani', 'title': 'Sovrani'}, day=2),
            FakeDoc('artworks', 'a6', {'slug': 'Sovrani', 'title': 'Sovrani'}, day=3),
            FakeDoc('artworks', 'a7', {'slug': 'ok-slug', 'title': 'Other'}),
            FakeDoc('artworks', 'a8', {'slug': '', 'title': 'Senza slug'}),
        ]
        out = plan_slugs('artworks', docs)
        self.assertNotIn('a1', out)                                   # oldest duplicate keeps it
        self.assertEqual(out['a2'], ('albero', 'albero-2', 'duplicate'))
        self.assertEqual(out['a3'], ('Padre e figlio', 'padre-e-figlio', 'non-conforming'))
        self.assertEqual(out['a4'], ('diana---sottobosco', 'diana-sottobosco', 'non-conforming'))
        self.assertEqual(out['a6'], ('Sovrani', 'sovrani-2', 'non-conforming'))  # collides with a5
        self.assertNotIn('a7', out)
        self.assertEqual(out['a8'], ('', 'senza-slug', 'empty'))

    def test_title_consistent_duplicate_keeps_slug(self):
        docs = [
            FakeDoc('techniques', 't1', {'slug': 'tecnica-mista', 'name': 'Acrilico su tela'}, day=1),
            FakeDoc('techniques', 't2', {'slug': 'tecnica-mista', 'name': 'Tecnica mista'}, day=4),
        ]
        out = plan_slugs('techniques', docs)
        self.assertNotIn('t2', out)
        self.assertEqual(out['t1'], ('tecnica-mista', 'acrilico-su-tela', 'duplicate'))

    def test_contents_underscore_untouched_and_categories_report_only(self):
        self.assertEqual(plan_slugs('contents', [FakeDoc('contents', 'c', {'slug': 'homepage_hero', 'title': 'Hero'})]), {})
        out = plan_slugs('categories', [FakeDoc('categories', 'k', {'slug': 'Bad Slug', 'name': 'Bad'})])
        self.assertEqual(out['k'][2], 'report-only: non-conforming')

    def test_idempotent(self):
        docs = [FakeDoc('artworks', 'a2', {'slug': 'albero-2', 'title': 'Albero'}),
                FakeDoc('artworks', 'a1', {'slug': 'albero', 'title': 'Albero'})]
        self.assertEqual(plan_slugs('artworks', docs), {})


class FilePlanning(unittest.TestCase):
    def _state(self, cover_path, gallery_paths, alt, cover_cache=None):
        art = FakeDoc('artworks', 'A', {'slug': 'nonna', 'title': 'Nonna', 'coverImage': {'original': url(cover_path), 'alt': alt}})
        gallery = [FakeDoc('artworks/A/gallery', f'g{i}', {'original': url(p), 'alt': a, 'imagePosition': pos, 'uploadedAt': f'2026-07-0{i}'})
                   for i, (p, a, pos) in enumerate(gallery_paths, start=1)]
        state = {'docs': {c: [] for c in ['artworks', 'series', 'contents', 'techniques', 'categories']}, 'gallery': {'A': gallery}}
        state['docs']['artworks'] = [art]
        blobs = {cover_path: FakeBlob(cover_path, cover_cache)}
        blobs.update({p: FakeBlob(p) for p, _, _ in gallery_paths})
        return state, blobs

    def test_nonna_example(self):
        state, blobs = self._state('artworks/8eea/495658824_1234_n.jpg', [
            ('artworks/A/gallery/u1/IMG_2.JPG', 'IMG_2', 2000),
            ('artworks/A/gallery/u2/IMG_1.JPG', 'Dettaglio del volto', 1000),
            ('artworks/A/gallery/u3/x.jpeg', '', None),
        ], alt='495658824_1234_n')
        plans = {p.doc_id: p for p in build_plan(state, blobs)}
        cover = plans['A'].images[0]
        self.assertEqual(cover.new_path, 'artworks/8eea/nonna.jpg')
        self.assertEqual(cover.new_alt, 'Nonna')
        self.assertFalse(plans['A'].slug_changes)
        g = {k.split('/')[-1]: v.images[0] for k, v in plans.items() if '/gallery/' in k}
        self.assertEqual(g['g2'].new_path, 'artworks/A/gallery/u2/nonna-1.jpg')   # imagePosition 1000 first
        self.assertEqual(g['g2'].new_alt, 'Dettaglio del volto')                  # descriptive alt kept
        self.assertEqual(g['g1'].new_path, 'artworks/A/gallery/u1/nonna-2.jpg')
        self.assertEqual(g['g3'].new_path, 'artworks/A/gallery/u3/nonna-3.jpeg')  # no position → last
        self.assertEqual(g['g3'].new_alt, 'Nonna')

    def test_already_normalized_is_noop(self):
        cc = 'public, max-age=31536000, immutable'
        state, blobs = self._state('artworks/8eea/nonna.jpg', [], alt='Nonna', cover_cache=cc)
        cover = build_plan(state, blobs)[0].images[0]
        self.assertFalse(cover.rename)
        self.assertFalse(cover.alt_changes)
        self.assertEqual(cover.cache_control, 'already')


if __name__ == '__main__':
    unittest.main()
