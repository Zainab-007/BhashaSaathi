import html, json


def _translation_map(translations):
    return {t.target_language: t.translated_text for t in (translations or [])}


def make_worksheet(lesson, version, translations=None, language_a=None, language_b=None):
    concepts = json.loads(version.concepts_json or '[]')
    texts = _translation_map(translations)
    source = version.clean_transcript or version.source_text
    language_a = language_a or lesson.source_language
    targets = [lang for lang in ('eng_Latn', 'hin_Deva', 'mar_Deva', 'sat_Olck') if lang != language_a]
    language_b = language_b or (targets[0] if targets else 'eng_Latn')
    target = texts.get(language_b, 'Translation not prepared yet.')
    english = texts.get('eng_Latn', source if lesson.source_language == 'eng_Latn' else 'Translation not prepared yet.')
    title = html.escape(lesson.title)

    vocab = ''.join(
        f'<li><strong>{html.escape(c.get("label", c.get("concept_id", "Concept")))}</strong></li>'
        for c in concepts[:8]
    ) or '<li>No approved concepts yet.</li>'
    body = f'''<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
    <style>body{{font-family:Arial,sans-serif;max-width:820px;margin:32px auto;padding:0 24px;color:#17352f}}
    h1{{margin-bottom:6px}}h2{{font-size:17px;margin-bottom:8px}}.box{{border:1px solid #d7dedb;border-radius:16px;padding:18px;margin:18px 0}}
    .grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}.label{{font-weight:700;color:#0b6759;margin-bottom:6px}}
    li{{margin:8px 0}}.line{{border-bottom:1px solid #b9c5c1;height:28px;margin:4px 0}}
    @media print{{body{{margin:10mm auto}}.box{{break-inside:avoid}}}}</style></head><body>
    <h1>{title}</h1>
    <div class="box"><h2>Vocabulary / शब्दावली</h2><ul>{vocab}</ul></div>
    <div class="box"><h2>Read & understand</h2><div class="grid">
      <div><div class="label">{html.escape(language_a)}</div><p>{html.escape(source)}</p></div>
      <div><div class="label">{html.escape(language_b)}</div><p>{html.escape(target)}</p></div>
    </div></div>
    <div class="box"><h2>English support</h2><p>{html.escape(english)}</p></div>
    <div class="box"><h2>Practice / अभ्यास</h2><ol>
      {''.join(f'<li>Explain <strong>{html.escape(c.get("label", "this concept"))}</strong> in your own words.<div class="line"></div><div class="line"></div></li>' for c in concepts[:4])}
    </ol></div>
    <div class="box"><h2>Student</h2><p>Name: ____________________________ &nbsp;&nbsp; Date: ______________</p></div>
    </body></html>'''
    return {
        'title': lesson.title,
        'learning_outcome': '',
        'concepts': concepts,
        'language_a': language_a,
        'language_b': language_b,
        'html': body,
    }
