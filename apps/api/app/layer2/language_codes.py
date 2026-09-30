LANGUAGES = {
    'eng_Latn': {'name':'English','native':'English','script':'latin'},
    'hin_Deva': {'name':'Hindi','native':'हिन्दी','script':'devanagari'},
    'mar_Deva': {'name':'Marathi','native':'मराठी','script':'devanagari'},
    'sat_Olck': {'name':'Santali','native':'ᱥᱟᱱᱛᱟᱲᱤ','script':'olchiki'},
}


def validate_language(code: str) -> None:
    if code not in LANGUAGES:
        raise ValueError(f'Unsupported language code: {code}')
