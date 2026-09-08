from evals.text_similarity import content_hash, is_near_duplicate, jaccard, normalize_text, shingles


def test_normalize_text_collapses_whitespace_and_casefolds() -> None:
    assert normalize_text("  Hello   World  ") == "hello world"


def test_content_hash_is_stable_and_whitespace_insensitive() -> None:
    assert content_hash("가로수가 쓰러졌어요") == content_hash("가로수가   쓰러졌어요 ")


def test_content_hash_differs_for_different_text() -> None:
    assert content_hash("가로수가 쓰러졌어요") != content_hash("가로등이 꺼졌어요")


def test_shingles_of_short_text_is_single_shingle() -> None:
    assert shingles("ab", n=5) == {"ab"}


def test_shingles_of_empty_text_is_empty_set() -> None:
    assert shingles("", n=5) == set()


def test_jaccard_identical_sets_is_one() -> None:
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_disjoint_sets_is_zero() -> None:
    assert jaccard({"a"}, {"b"}) == 0.0


def test_jaccard_both_empty_is_one() -> None:
    assert jaccard(set(), set()) == 1.0


def test_is_near_duplicate_true_for_trailing_punctuation_diff() -> None:
    a = "분당구 정자동에서 가로수가 쓰러져 인도를 막고 있습니다."
    b = "분당구 정자동에서 가로수가 쓰러져 인도를 막고 있습니다!"
    assert is_near_duplicate(a, b) is True


def test_is_near_duplicate_false_for_unrelated_text() -> None:
    a = "분당구 정자동에서 가로수가 쓰러져 인도를 막고 있습니다."
    b = "수정구 신흥동에서 가스 냄새가 심하게 납니다."
    assert is_near_duplicate(a, b) is False
