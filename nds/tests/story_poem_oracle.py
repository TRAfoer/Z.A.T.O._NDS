"""Independent, source-glyph-only reference for the two trilingual poems.

This module deliberately does not import a producer, packer or story renderer.
The caller supplies the existing independent ``measure_glyph`` as a closure.
"""
import unittest


POEM_RECORDS = {
    11952: (3424, 3432),
    11953: (3424, 3432),
    11960: (14646, 14654),
    11961: (14646, 14654),
}


def _hard_lines(glyphs):
    """Keep newline in its preceding line; a final newline starts an empty one."""
    stops = [0] + [i + 1 for i, g in enumerate(glyphs) if g['char'] == '\n'] + [len(glyphs)]
    return [list(range(a, b)) for a, b in zip(stops, stops[1:])]


def poem_pages(ru_glyphs, en_glyphs, cn_glyphs, measure):
    """Return independent lower/upper pages and authored-line group metadata.

    ``measure(glyph, font)`` returns ink_width, ink_height, advance. It must be
    based on source fonts, not the producer's glyph directory. Newlines are not
    measured. Foreign indexes are relative to their own stream: all RU then EN;
    the pack oracle may add CN count when checking its EN-command plane.

    Blank authored lines occupy a complete group. Fixed native metrics give a
    22px upper group (RU at 0, EN at 14), 26px group pitch, and 12/16px lower
    height/pitch. Both blocks are centered in y24..168; each line is centered
    using maximum ink extent including the supplied canvas extent for spaces.
    """
    sources = {'ru': ru_glyphs, 'en': en_glyphs, 'cn': cn_glyphs}
    lines = {language: _hard_lines(glyphs) for language, glyphs in sources.items()}
    counts = {len(rows) for rows in lines.values()}
    if len(counts) != 1:
        raise ValueError('Poem RU/EN/CN authored hard-line counts differ')
    count = counts.pop()
    page_count = (count + 4) // 5
    quotient, extra = divmod(count, page_count)
    sizes = [quotient + (page < extra) for page in range(page_count)]
    upper_pages, lower_pages, groups = [], [], []

    def entries(language, indices, y, page, group):
        font = 8 if language == 'en' else 12
        relative = len(ru_glyphs) if language == 'en' else 0
        pen = 0
        line = []
        extent = 0
        for index in indices:
            g = dict(sources[language][index], language='cn' if language == 'cn' else 'en', font=font)
            if g['char'] == '\n':
                width = height = advance = 0
            else:
                width, height, advance = measure(g, font)
                if width < 0 or height < 0 or advance < 0 or height > font:
                    raise ValueError('Poem source exceeds the fixed native font metrics')
                extent = max(extent, pen + width)
            line.append(dict(index=index + relative, source_index=index, glyph=g,
                             language=language, group=group, font=font, x=pen, y=y,
                             page=page, width=advance, height=height, ink_width=width))
            pen += advance
        if extent > 240:
            raise ValueError('Poem authored line exceeds the x8..248 ink area')
        shift = (256 - extent) // 2
        for item in line:
            item['x'] += shift
        return line

    group_index = 0
    for page, size in enumerate(sizes):
        upper_top = 24 + (144 - ((size - 1) * 26 + 22)) // 2
        lower_top = 24 + (144 - ((size - 1) * 16 + 12)) // 2
        upper, lower, page_groups = [], [], []
        for row in range(size):
            ru_y, en_y, cn_y = upper_top + row * 26, upper_top + row * 26 + 14, lower_top + row * 16
            group = dict(index=group_index, page=page, row=row, ru_y=ru_y, en_y=en_y, cn_y=cn_y,
                         ru_indices=lines['ru'][group_index], en_indices=lines['en'][group_index],
                         cn_indices=lines['cn'][group_index])
            upper.extend(entries('ru', group['ru_indices'], ru_y, page, group_index))
            upper.extend(entries('en', group['en_indices'], en_y, page, group_index))
            lower.extend(entries('cn', group['cn_indices'], cn_y, page, group_index))
            page_groups.append(group)
            group_index += 1
        upper_pages.append(upper)
        lower_pages.append(lower)
        groups.append(page_groups)
    assert sorted(e['index'] for page in lower_pages for e in page) == list(range(len(cn_glyphs)))
    assert sorted(e['index'] for page in upper_pages for e in page) == list(range(len(ru_glyphs) + len(en_glyphs)))
    return dict(lower_pages=lower_pages, upper_pages=upper_pages, groups=groups)


class PoemReferenceTests(unittest.TestCase):
    @staticmethod
    def glyphs(text, language):
        return [dict(char=c, language=language, color=(255, 255, 255)) for c in text]

    @staticmethod
    def measure(glyph, font):
        return font, font, font // 2 if glyph['language'] != 'cn' else font

    def layout(self, lines):
        ru = self.glyphs('\n'.join(lines), 'en')
        en = self.glyphs('\n'.join(lines), 'en')
        cn = self.glyphs('\n'.join(lines), 'cn')
        return poem_pages(ru, en, cn, self.measure)

    def test_eight_authored_lines_balance_four_and_four(self):
        result = self.layout(['a'] * 8)
        self.assertEqual([len(page) for page in result['groups']], [4, 4])
        self.assertEqual([g['ru_y'] for g in result['groups'][0]], [46, 72, 98, 124])
        self.assertEqual([g['en_y'] for g in result['groups'][0]], [60, 86, 112, 138])
        self.assertEqual([g['cn_y'] for g in result['groups'][0]], [66, 82, 98, 114])

    def test_fourteen_lines_keep_blank_groups_and_every_logical_index(self):
        lines = ['a'] * 14
        lines[4] = lines[9] = ''
        result = self.layout(lines)
        self.assertEqual([len(page) for page in result['groups']], [5, 5, 4])
        self.assertEqual([g['cn_y'] for g in result['groups'][0]], [58, 74, 90, 106, 122])
        blank = result['groups'][0][4]
        entries = [e for e in result['upper_pages'][0] if e['group'] == blank['index']]
        self.assertTrue(entries)
        self.assertTrue(all(e['glyph']['char'] == '\n' for e in entries))
        text = '\n'.join(lines)
        cn = [e for page in result['lower_pages'] for e in page]
        foreign = [e for page in result['upper_pages'] for e in page]
        self.assertEqual(sorted(e['index'] for e in cn), list(range(len(text))))
        self.assertEqual(sorted(e['index'] for e in foreign), list(range(len(text) * 2)))
        self.assertEqual(''.join(e['glyph']['char'] for e in sorted(foreign, key=lambda e: e['index'])), text * 2)

    def test_center_uses_canvas_ink_extent_not_advance(self):
        result = self.layout(['aa'] * 8)
        ru = [e for e in result['upper_pages'][0] if e['language'] == 'ru' and e['group'] == 0]
        en = [e for e in result['upper_pages'][0] if e['language'] == 'en' and e['group'] == 0]
        cn = [e for e in result['lower_pages'][0] if e['group'] == 0]
        self.assertEqual([e['x'] for e in ru[:2]], [119, 125])
        self.assertEqual([e['x'] for e in en[:2]], [122, 126])
        self.assertEqual([e['x'] for e in cn[:2]], [116, 128])

    def test_mismatching_authored_hard_lines_are_rejected(self):
        with self.assertRaises(ValueError):
            poem_pages(self.glyphs('a\nb', 'en'), self.glyphs('a', 'en'), self.glyphs('a\nb', 'cn'), self.measure)


if __name__ == '__main__':
    unittest.main()
