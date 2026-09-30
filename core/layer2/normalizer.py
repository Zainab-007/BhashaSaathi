import re

def normalize_text(text: str) -> str:
    value = text.replace('\r\n','\n').replace('\r','\n').strip()
    value = re.sub(r'[ \t]+', ' ', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value
