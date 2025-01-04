import logging

logger = logging.getLogger(__name__)

# Define a simple Arabic to Latin transliteration mapping
TRANSLITERATION_MAP = {
    'ا': 'a',
    'ب': 'b',
    'ت': 't',
    'ث': 'th',
    'ج': 'j',
    'ح': 'h',
    'خ': 'kh',
    'د': 'd',
    'ذ': 'dh',
    'ر': 'r',
    'ز': 'z',
    'س': 's',
    'ش': 'sh',
    'ص': 's',
    'ض': 'd',
    'ط': 't',
    'ظ': 'z',
    'ع': 'a',
    'غ': 'gh',
    'ف': 'f',
    'ق': 'q',
    'ك': 'k',
    'ل': 'l',
    'م': 'm',
    'ن': 'n',
    'ه': 'h',
    'و': 'w',
    'ي': 'y',
    'ء': 'a',
    'ؤ': 'w',
    'ئ': 'y',
    'ة': 'a',
    'ى': 'a',
    'ۤ': 'a',
    'ﻻ': 'la',
    'ﻷ': 'laa',
    'ﻹ': 'laa',
    'ﻵ': 'laa',
}

def transliterate_arabic(text: str) -> str:
    """Transliterate Arabic text to Latin."""
    transliterated = ''.join(TRANSLITERATION_MAP.get(char, char) for char in text)
    logger.debug(f"Transliterated '{text}' to '{transliterated}'")
    return transliterated
