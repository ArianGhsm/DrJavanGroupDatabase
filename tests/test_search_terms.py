from drjavanbot.search.terms import informative_query, informative_tokens


def test_e_max_compound_keeps_single_latin_component():
    assert informative_query("e.max") == "e max"
    assert informative_query("برای e.max چه تجربه‌ای دارید؟") == "e max تجربه دارید"


def test_x_ray_compound_keeps_single_latin_component_but_noise_stays_removed():
    assert informative_query("x-ray خوبه؟") == "x ray"
    assert informative_query("a good x-ray") == "x ray"


def test_persian_single_character_noise_is_not_readmitted():
    tokens = informative_tokens("و به از کامپوزیت")
    assert tokens == ("کامپوزیت",)
