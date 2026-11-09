from datetime import datetime, timezone

from pulse.dates import RedditIdClock, is_within_hours, linkedin_date_from_url, resolve_reddit_date
from pulse.urls import (mention_key, reddit_thread_id, subreddit_from_url, thread_key,
                        unwrap_f5bot, is_reddit_thread_url)

F5 = ("https://f5bot.com/url?u=https%3A%2F%2Fwww.reddit.com%2Fr%2Fstartup%2Fcomments%2F1tkp3d8%2Fc%2Fp3gw4l3"
      "%3Fcontext%3D3&i=79446089&h=b04d698f")


def test_f5bot_wrapper_is_unwrapped():
    assert unwrap_f5bot(F5).startswith("https://www.reddit.com/r/startup/comments/1tkp3d8")
    assert subreddit_from_url(F5) == "startup"


def test_host_variants_share_a_thread_key():
    a = "https://www.reddit.com/r/pdf/comments/1abc23/some_title/"
    b = "https://old.reddit.com/r/pdf/comments/1abc23/some_title/?utm_source=x"
    c = "https://sh.reddit.com/r/pdf/comments/1abc23"
    assert thread_key(a) == thread_key(b) == thread_key(c) == "reddit:1abc23"


def test_comment_permalinks_collapse_to_thread_but_keep_mention_key():
    thread = "https://www.reddit.com/r/pdf/comments/1abc23/some_title/"
    comment = "https://www.reddit.com/r/pdf/comments/1abc23/some_title/k9x8y7z/"
    assert thread_key(comment) == thread_key(thread)
    assert mention_key(comment) != mention_key(thread)
    assert mention_key(F5) == "reddit:1tkp3d8:p3gw4l3"


def test_only_threads_count_as_leads():
    assert is_reddit_thread_url("https://www.reddit.com/r/x/comments/1abc23/t/")
    assert not is_reddit_thread_url("https://www.reddit.com/user/brand-official/")
    assert not is_reddit_thread_url("https://www.reddit.com/r/Gnostic/hot/?tl=pt-br")
    assert not is_reddit_thread_url("https://example.com/r/x/comments/1abc23/")


def test_non_reddit_keys_are_normalised():
    assert thread_key("https://www.linkedin.com/posts/x-activity-7510856898120773633-Po7R?utm=1") == "linkedin:7510856898120773633"
    assert thread_key("https://www.example.com/a/b/?q=1") == "example.com/a/b"


def _clock():
    base = int("1w0000a", 36)
    # 1 id-step ~ 1 second, anchors an hour apart on both ends
    return base, RedditIdClock([[base, 1_700_000_000], [base + 200_000, 1_700_200_000]])


def test_clock_interpolates_between_anchors():
    base, clock = _clock()
    assert abs(clock.estimate(base + 100_000) - 1_700_100_000) < 1


def test_clock_needs_two_anchors():
    assert RedditIdClock([[1, 1.0]]).estimate(5) is None


def test_clock_extrapolates_but_never_into_the_future():
    base, clock = _clock()
    older = clock.estimate(base - 1000)
    assert older < 1_700_000_000
    future = clock.estimate(base + 10**9)
    assert future <= datetime.now(timezone.utc).timestamp()


def test_clock_thins_anchor_list():
    clock = RedditIdClock([[i, 1_700_000_000 + i * 60] for i in range(1, 1001)], max_anchors=100)
    assert len(clock.to_list()) == 100


def test_date_resolution_order():
    base, clock = _clock()
    now = "2026-09-30T12:00:00+00:00"
    tid = "1w0000a"
    iso, conf = resolve_reddit_date(tid, "Sep 28, 2026", "", clock, now)
    assert conf == "approx" and iso.startswith("2026-09-28")
    iso, conf = resolve_reddit_date(tid, None, "posted 3 days ago - foo", clock, now)
    assert conf == "approx"
    iso, conf = resolve_reddit_date("1w0000b", None, "", clock, now)
    assert conf == "estimated"
    iso, conf = resolve_reddit_date(None, None, "", clock, now)
    assert (iso, conf) == (now, "unknown")


def test_freshness_helpers():
    assert not is_within_hours("2020-01-01T00:00:00+00:00", 48)
    assert is_within_hours("garbage", 48)  # never drop a lead on a bad date
    assert is_within_hours(datetime.now(timezone.utc).isoformat(), 48)


def test_linkedin_snowflake_date():
    url = "https://www.linkedin.com/posts/x-activity-7510856898120773633-Po7R"
    assert linkedin_date_from_url(url).startswith("2026-09-30")


# ---------- Quora & Google Alerts (hub additions) ----------
from pulse.urls import platform_of_url, quora_question_slug, unwrap_google  # noqa: E402


def test_quora_questions_are_identified_and_answers_collapse_to_their_question():
    q = "https://www.quora.com/How-do-I-compress-a-PDF-file"
    a = "https://www.quora.com/How-do-I-compress-a-PDF-file/answer/Jane-Doe-12?utm_source=x"
    assert quora_question_slug(q) == "How-do-I-compress-a-PDF-file"
    assert thread_key(q) == thread_key(a) == "quora:how-do-i-compress-a-pdf-file"
    assert platform_of_url(q) == "quora"
    assert thread_key("https://es.quora.com/Como-comprimir-un-PDF").startswith("quora:es:")


def test_quora_non_question_pages_are_not_leads():
    for url in ("https://www.quora.com/profile/Jane-Doe", "https://www.quora.com/topic/PDF",
                "https://www.quora.com/search?q=pdf", "https://www.quora.com/", "https://www.quora.com/about"):
        assert quora_question_slug(url) is None and platform_of_url(url) is None


def test_google_alert_redirects_are_unwrapped():
    wrapped = ("https://www.google.com/url?rct=j&sa=t&url=https://www.reddit.com/r/pdf/comments/1abc23/t/"
               "&ct=ga&cd=CAEYAA&usg=AOvVaw")
    assert unwrap_google(wrapped) == "https://www.reddit.com/r/pdf/comments/1abc23/t/"
    assert thread_key(wrapped) == "reddit:1abc23" and platform_of_url(wrapped) == "reddit"
    assert unwrap_google("https://example.com/url?url=x") == "https://example.com/url?url=x"
