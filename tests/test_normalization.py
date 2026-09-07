from drjavanbot.normalization import normalize_text, tokenize


def test_persian_arabic_letters_digits_and_zwnj_are_unified():
    assert normalize_text("ي ك آ ۱۲٣ درمان‌ریشه") == "ی ک ا 123 درمان ریشه"


def test_whitespace_punctuation_and_english_casefold():
    assert normalize_text("  E.MAX,   RCT!\nTest ") == "e max rct test"
    assert tokenize("RCT / درمان‌ریشه") == ("rct", "درمان", "ریشه")
