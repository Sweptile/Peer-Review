from peer_review.review_parsing import parse_peer_review
from peer_review.rubric import RubricItem

RUBRIC = [
    RubricItem(question="Q1", label="Correctness", max_points="10"),
    RubricItem(question="Q2", label="Code Style", max_points="5"),
]


def test_basic_two_question_extraction():
    body = (
        "Self Assessment:\n"
        "Q1 - Correctness (out of 10):\n9 - I think I nailed it.\n\n"
        "Q2 - Code Style (out of 5):\n4 - could be cleaner.\n\n"
        "Peer Review:\n"
        "Q1 - Correctness (out of 10):\n8 - great structure.\n\n"
        "Q2 - Code Style (out of 5):\n5 - very clean.\n"
    )
    forward_text, error = parse_peer_review(body, RUBRIC)
    assert error is None
    assert "8 - great structure." in forward_text
    assert "5 - very clean." in forward_text
    # Self-assessment content must never leak into the forwarded text.
    assert "nailed it" not in forward_text
    assert "could be cleaner" not in forward_text


def test_reconstructed_headers_use_rubric_wording_not_students():
    # Student writes sloppy/inconsistent headers; only the marker itself
    # ("Q1:", case/space-insensitive) needs to match -- the reconstructed
    # output should use the rubric's own canonical header text.
    body = (
        "Peer Review:\n"
        "q1  :   correctness was fine\n8/10, solid work\n\n"
        "Q2:\nstyle notes\n4/5, minor nitpicks\n"
    )
    forward_text, error = parse_peer_review(body, RUBRIC)
    assert error is None
    assert "Q1 - Correctness (out of 10):" in forward_text
    assert "Q2 - Code Style (out of 5):" in forward_text
    assert "8/10, solid work" in forward_text
    assert "4/5, minor nitpicks" in forward_text


def test_peer_review_section_can_come_before_self_assessment():
    body = (
        "Peer Review:\n"
        "Q1 - Correctness (out of 10):\n8 - good.\n\n"
        "Q2 - Code Style (out of 5):\n5 - clean.\n\n"
        "Self Assessment:\n"
        "Q1 - Correctness (out of 10):\n9 - proud of this.\n\n"
        "Q2 - Code Style (out of 5):\n4 - okay.\n"
    )
    forward_text, error = parse_peer_review(body, RUBRIC)
    assert error is None
    assert "8 - good." in forward_text
    assert "proud of this" not in forward_text


def test_canvas_rich_text_html_wrapping():
    body = (
        "<p><strong>Peer Review:</strong></p>"
        "<p>Q1 - Correctness (out of 10):</p>"
        "<p>8 - great structure.</p>"
        "<p>Q2 - Code Style (out of 5):</p>"
        "<p>5 - very clean.</p>"
    )
    forward_text, error = parse_peer_review(body, RUBRIC)
    assert error is None
    assert "great structure" in forward_text
    assert "very clean" in forward_text


def test_html_entities_are_decoded():
    body = (
        "Peer Review:\n"
        "Q1 - Correctness (out of 10):\nUses &amp; correctly, isn&#39;t broken.\n\n"
        "Q2 - Code Style (out of 5):\nFine.\n"
    )
    forward_text, error = parse_peer_review(body, RUBRIC)
    assert error is None
    assert "Uses & correctly, isn't broken." in forward_text


def test_missing_peer_review_section_returns_error():
    body = "Self Assessment:\nQ1 - Correctness (out of 10):\n9 - good.\nQ2 - Code Style (out of 5):\n4 - ok.\n"
    forward_text, error = parse_peer_review(body, RUBRIC)
    assert forward_text is None
    assert "Peer Review" in error


def test_duplicate_peer_review_section_returns_error():
    body = "Peer Review:\nfirst\n\nPeer Review:\nsecond"
    forward_text, error = parse_peer_review(body, RUBRIC)
    assert forward_text is None
    assert "Peer Review" in error


def test_missing_question_header_returns_error():
    body = "Peer Review:\nQ1 - Correctness (out of 10):\n8 - good.\n"  # Q2 missing entirely
    forward_text, error = parse_peer_review(body, RUBRIC)
    assert forward_text is None
    assert "Q2" in error
    assert "missing" in error


def test_duplicate_question_header_returns_error():
    body = (
        "Peer Review:\n"
        "Q1 - Correctness (out of 10):\nfirst answer\n\n"
        "Q1 - Correctness (out of 10):\nsecond answer\n\n"
        "Q2 - Code Style (out of 5):\nfine\n"
    )
    forward_text, error = parse_peer_review(body, RUBRIC)
    assert forward_text is None
    assert "Q1" in error
    assert "more than once" in error


def test_empty_answer_returns_error():
    body = "Peer Review:\nQ1 - Correctness (out of 10):\n\nQ2 - Code Style (out of 5):\nfine\n"
    forward_text, error = parse_peer_review(body, RUBRIC)
    assert forward_text is None
    assert "Q1" in error
    assert "no answer" in error


def test_empty_body_returns_error():
    forward_text, error = parse_peer_review(None, RUBRIC)
    assert forward_text is None
    assert error is not None

    forward_text, error = parse_peer_review("", RUBRIC)
    assert forward_text is None
    assert error is not None


def test_single_question_rubric():
    single = [RubricItem(question="Q1", label="Overall", max_points="20")]
    body = "Peer Review:\nQ1 - Overall (out of 20):\n18 - excellent.\n"
    forward_text, error = parse_peer_review(body, single)
    assert error is None
    assert forward_text == "Q1 - Overall (out of 20):\n18 - excellent."


def test_header_wrapped_across_lines_still_parses():
    # A long header pasted into Canvas's editor can break across paragraphs.
    # The packet's exact header is still there once whitespace is normalized.
    body = (
        "Peer Review:\n"
        "Q1 - Correctness (out of 10):\n8 - good.\n\n"
        "Q2 - Code\n\nStyle (out of 5):\n5 - clean.\n"
    )
    forward_text, error = parse_peer_review(body, RUBRIC)
    assert error is None
    assert "8 - good." in forward_text
    assert "5 - clean." in forward_text
    # The wrapped header's tail must not leak into the answer text.
    assert "Style (out of 5):\n5 - clean." in forward_text
    assert "Code\n" not in forward_text


def test_label_containing_a_colon_does_not_leak_into_the_answer():
    colon_rubric = [RubricItem(question="Q1", label="P1(a): setup", max_points="2")]
    body = "Peer Review:\nQ1 - P1(a): setup (out of 2):\n2 - fine.\n"
    forward_text, error = parse_peer_review(body, colon_rubric)
    assert error is None
    assert forward_text == "Q1 - P1(a): setup (out of 2):\n2 - fine."


def test_loose_fallback_still_works_when_header_is_not_canonical():
    body = "Peer Review:\nq1: 8 - good\n\nQ2 - my own wording:\n5 - clean\n"
    forward_text, error = parse_peer_review(body, RUBRIC)
    assert error is None
    assert "8 - good" in forward_text
    assert "5 - clean" in forward_text
