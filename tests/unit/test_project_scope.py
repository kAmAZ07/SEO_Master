"""Область проекта: нормальные, граничные, некорректные и вредоносные адреса (AC-03)."""
import pytest

from shared.projects.scope import ProjectScope, ScopeError, parse_scope, scopes_overlap


def scope(url):
    return parse_scope(url)


# --- нормализация -------------------------------------------------------------

@pytest.mark.parametrize(
    "url, origin, prefix",
    [
        ("https://example.com", "https://example.com", "/"),
        ("https://Example.COM/", "https://example.com", "/"),
        ("https://example.com:443/", "https://example.com", "/"),
        ("http://example.com:80/blog/", "http://example.com", "/blog"),
        ("https://example.com/blog?utm=1#top", "https://example.com", "/blog"),
        ("https://example.com/a/./b/../c", "https://example.com", "/a/c"),
        ("https://example.com/%62log", "https://example.com", "/blog"),
        ("https://пример.рф/каталог/", "https://xn--e1afmkfd.xn--p1ai", "/%D0%BA%D0%B0%D1%82%D0%B0%D0%BB%D0%BE%D0%B3"),
        ("https://example.com./", "https://example.com", "/"),
        ("  https://example.com/x  ", "https://example.com", "/x"),
    ],
)
def test_normalization(url, origin, prefix):
    result = scope(url)
    assert (result.origin, result.path_prefix) == (origin, prefix)


def test_origin_wide_flag():
    assert scope("https://example.com/").is_origin_wide
    assert not scope("https://example.com/blog").is_origin_wide


def test_www_and_bare_domain_are_different_scopes():
    assert not scope("https://example.com/").contains("https://www.example.com/")
    assert not scope("https://www.example.com/").contains("https://example.com/")


def test_scheme_and_port_must_match_exactly():
    base = scope("https://example.com/")
    assert not base.contains("http://example.com/")
    assert not base.contains("https://example.com:8443/")
    assert scope("http://example.com/").contains("http://example.com:80/page")


def test_scheme_and_port_are_checked_independently():
    """Разрешённые порты 80 и 443 можно сочетать со «чужой» схемой: каждое поле сравнивается отдельно."""
    https_scope, http_scope = scope("https://example.com/"), scope("http://example.com/")
    assert not https_scope.contains("https://example.com:80/")  # та же схема, другой порт
    assert not https_scope.contains("http://example.com:443/")  # тот же порт, другая схема
    assert not http_scope.contains("http://example.com:443/")
    assert not http_scope.contains("https://example.com:80/")
    assert https_scope.contains("https://example.com:443/")


# --- принадлежность -------------------------------------------------------------

@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://example.com/blog", True),
        ("https://example.com/blog/", True),
        ("https://example.com/blog/post-1?x=1#y", True),
        ("https://example.com/blog/%70ost", True),
        ("https://example.com/blogger", False),  # префикс только по границе сегмента
        ("https://example.com/blo", False),
        ("https://example.com/", False),
        ("https://example.com/Blog/post", False),  # путь чувствителен к регистру
        ("https://example.com/blog/../admin", False),
        ("https://example.com/blog/%2e%2e/admin", False),
        ("https://example.com/blog%2f..%2fadmin", False),
        ("https://example.com/blog/..%2fadmin", False),
        ("https://example.com/blog\\..\\admin", False),
        ("https://example.com//blog", False),
        ("https://example.com/blog//x", False),
        ("https://other.com/blog", False),
        ("https://example.com.evil.com/blog", False),
        ("https://example.com@evil.com/blog", False),
        ("ftp://example.com/blog", False),
        ("not a url", False),
        ("", False),
    ],
)
def test_contains_prefix_scope(url, expected):
    assert scope("https://example.com/blog").contains(url) is expected


def test_origin_wide_scope_contains_every_path_on_the_host():
    base = scope("https://example.com/")
    assert base.contains("https://example.com/a/b?c=d")
    assert not base.contains("https://example.com/../")


def test_traversal_that_stays_inside_is_resolved_then_checked():
    assert scope("https://example.com/blog").contains("https://example.com/blog/a/../b")
    assert not scope("https://example.com/blog/a").contains("https://example.com/blog/a/../b")


# --- некорректные области ------------------------------------------------------------

@pytest.mark.parametrize(
    "url, code",
    [
        (None, "invalid_url"),
        ("", "invalid_url"),
        ("   ", "invalid_url"),
        ("example.com", "scheme_not_allowed"),
        ("ftp://example.com/", "scheme_not_allowed"),
        ("javascript:alert(1)", "scheme_not_allowed"),
        ("https://user:pass@example.com/", "userinfo_not_allowed"),
        ("https://example.com:8080/", "port_not_allowed"),
        ("https://localhost/", "host_blocked"),
        ("https://2130706433/", "host_blocked"),
        ("https://example.com/a%2fb", "path_ambiguous"),
        ("https://example.com/a%5cb", "path_ambiguous"),
        ("https://example.com/a%00b", "path_ambiguous"),
        ("https://example.com/..", "path_ambiguous"),
        ("https://example.com/a//b", "path_ambiguous"),
        ("https://example.com/%ff", "path_ambiguous"),
    ],
)
def test_invalid_scopes_are_rejected_with_stable_code(url, code):
    with pytest.raises(ScopeError) as caught:
        parse_scope(url)
    assert caught.value.code == code


def test_path_length_limit():
    with pytest.raises(ScopeError) as caught:
        parse_scope("https://example.com/" + "a" * 1100)
    assert caught.value.code == "path_too_long"


# --- пересечение и вспомогательное --------------------------------------------------------

def test_overlap():
    root = scope("https://example.com/")
    blog = scope("https://example.com/blog")
    shop = scope("https://example.com/shop")
    assert scopes_overlap(root, blog) and scopes_overlap(blog, root)
    assert not scopes_overlap(blog, shop)
    assert not scopes_overlap(blog, scope("https://other.com/blog"))
    assert not scopes_overlap(blog, scope("http://example.com/blog"))


def test_challenge_url_for_origin_and_prefix():
    assert scope("https://example.com/").challenge_url("f.txt") == "https://example.com/f.txt"
    assert scope("https://example.com/blog/").challenge_url("f.txt") == "https://example.com/blog/f.txt"
    assert scope("http://example.com:80/").challenge_url("f.txt") == "http://example.com/f.txt"


def test_scope_is_immutable_value_object():
    value = scope("https://example.com/")
    with pytest.raises(Exception):
        value.host = "evil.com"  # type: ignore[misc]
    assert value == ProjectScope("https", "example.com", 443, "/")
