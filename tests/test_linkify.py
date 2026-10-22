from __future__ import annotations

from figai.linkify import label_for, shorten_links

LONG = "https://www.example.com/articles/2026/10/how-to-train-your-dragon?utm_source=x&utm_medium=y"


def test_long_url_becomes_labelled_link_keeping_the_real_url() -> None:
    assert shorten_links(f"See {LONG} for details.") == f"See <{LONG}|{label_for(LONG)}> for details."
    assert label_for(LONG).startswith("example.com/…/how-to-train")


def test_trailing_punctuation_stays_outside_the_link() -> None:
    assert shorten_links(f"({LONG}).") == f"(<{LONG}|{label_for(LONG)}>)."


def test_short_urls_are_left_alone() -> None:
    text = "the miner is at http://192.168.50.57/ ok"
    assert shorten_links(text) == text


def test_existing_slack_links_and_code_are_untouched() -> None:
    labelled = f"<{LONG}|my label>"
    assert shorten_links(labelled) == labelled
    bare_slack = f"<{LONG}>"
    assert shorten_links(bare_slack) == bare_slack
    code = f"run `curl {LONG}` now\n```\n{LONG}\n```"
    assert shorten_links(code) == code


def test_multiple_links_and_labels() -> None:
    other = "https://github.com/iamkeq/FigAi/pull/24/files/abcdef0123456789abcdef"
    out = shorten_links(f"{LONG}\n{other}")
    assert out.count("|") == 2
    assert label_for("https://example.com/") == "example.com"
    assert label_for("https://example.com/one") == "example.com/one"
    assert len(label_for("https://" + "a" * 80 + ".com/x/y")) <= 40


def test_url_inside_query_string_of_another_is_not_double_wrapped() -> None:
    out = shorten_links("https://redirect.example.com/go?to=https://target.example.com/some/very/long/path/here/now")
    assert out.count("<") == 1
