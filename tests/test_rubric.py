import pytest

from peer_review.rubric import RubricItem, format_question_header, read_rubric_csv, template_for


def write_csv(tmp_path, text):
    path = tmp_path / "rubric.csv"
    path.write_text(text)
    return str(path)


def test_read_rubric_csv(tmp_path):
    path = write_csv(
        tmp_path,
        "question,label,max_points\nQ1,Correctness,10\nQ2,Code Style,5\n",
    )
    items = read_rubric_csv(path)
    assert items == [
        RubricItem(question="Q1", label="Correctness", max_points="10"),
        RubricItem(question="Q2", label="Code Style", max_points="5"),
    ]


def test_max_points_is_optional(tmp_path):
    path = write_csv(tmp_path, "question,label\nQ1,Correctness\n")
    items = read_rubric_csv(path)
    assert items == [RubricItem(question="Q1", label="Correctness", max_points="")]


def test_rejects_duplicate_question_ids(tmp_path):
    path = write_csv(tmp_path, "question,label\nQ1,Correctness\nQ1,Style\n")
    with pytest.raises(ValueError, match="Duplicate question id"):
        read_rubric_csv(path)


def test_rejects_missing_required_column(tmp_path):
    path = write_csv(tmp_path, "question\nQ1\n")
    with pytest.raises(ValueError, match="missing required column"):
        read_rubric_csv(path)


def test_rejects_empty_rubric(tmp_path):
    path = write_csv(tmp_path, "question,label\n")
    with pytest.raises(ValueError, match="no rubric items"):
        read_rubric_csv(path)


def test_format_question_header_with_points():
    item = RubricItem(question="Q1", label="Correctness", max_points="10")
    assert format_question_header(item) == "Q1 - Correctness (out of 10):"


def test_format_question_header_without_points():
    item = RubricItem(question="Q1", label="Correctness")
    assert format_question_header(item) == "Q1 - Correctness:"


def test_template_for_includes_every_item_in_order():
    items = [
        RubricItem(question="Q1", label="Correctness", max_points="10"),
        RubricItem(question="Q2", label="Style", max_points="5"),
    ]
    template = template_for(items)
    assert template.index("Q1 - Correctness (out of 10):") < template.index("Q2 - Style (out of 5):")
    assert template.count("<your score and justification>") == 2
