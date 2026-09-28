from peer_review.review_parsing import extract_peer_review_section


def test_plain_text_peer_review_last():
    body = "Self Assessment:\nI think I did well.\n\nPeer Review:\nGreat structure, needs more tests."
    assert extract_peer_review_section(body) == "Great structure, needs more tests."


def test_plain_text_peer_review_first():
    body = "Peer Review:\nGreat structure, needs more tests.\n\nSelf Assessment:\nI think I did well."
    assert extract_peer_review_section(body) == "Great structure, needs more tests."


def test_canvas_rich_text_html_wrapping():
    body = (
        "<p><strong>Self Assessment:</strong></p>"
        "<p>I think I did well overall.</p>"
        "<p><strong>Peer Review:</strong></p>"
        "<p>Great structure, needs more tests.</p>"
        "<p>Also consider edge cases.</p>"
    )
    result = extract_peer_review_section(body)
    assert "Great structure, needs more tests." in result
    assert "Also consider edge cases." in result
    assert "I think I did well" not in result


def test_html_entities_are_decoded():
    body = "Peer Review:\nUses &amp; correctly, but &lt;script&gt; tags aren&#39;t escaped."
    result = extract_peer_review_section(body)
    assert result == "Uses & correctly, but <script> tags aren't escaped."


def test_case_insensitive_and_flexible_spacing():
    body = "PEER   REVIEW :\nLooks solid."
    assert extract_peer_review_section(body) == "Looks solid."


def test_missing_peer_review_marker_returns_none():
    body = "Self Assessment:\nI think I did well.\n\nJust some other notes, no header for the rest."
    assert extract_peer_review_section(body) is None


def test_duplicate_peer_review_marker_is_ambiguous():
    body = "Peer Review:\nFirst attempt.\n\nPeer Review:\nSecond attempt, ignore the first."
    assert extract_peer_review_section(body) is None


def test_duplicate_self_assessment_marker_is_ambiguous():
    body = (
        "Self Assessment:\nFirst.\n\nSelf Assessment:\nSecond.\n\nPeer Review:\nMy actual review."
    )
    assert extract_peer_review_section(body) is None


def test_no_self_assessment_header_still_extracts_peer_review():
    body = "Peer Review:\nGreat work overall, nice edge case handling."
    assert extract_peer_review_section(body) == "Great work overall, nice edge case handling."


def test_empty_peer_review_section_returns_none():
    body = "Peer Review:\n\nSelf Assessment:\nI wrote my self-assessment but forgot the peer review."
    assert extract_peer_review_section(body) is None


def test_empty_body_returns_none():
    assert extract_peer_review_section(None) is None
    assert extract_peer_review_section("") is None
    assert extract_peer_review_section("<p></p>") is None
