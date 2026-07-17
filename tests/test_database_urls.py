import pytest

from xbb import storage


@pytest.mark.parametrize(
    ("dsn", "expected"),
    [
        ("postgresql://user:pass@localhost:5432/xbookmarkbrain",
         "postgresql://user:pass@localhost:5432/xbookmarkbrain_test"),
        ("postgresql://user:p%40ss@db.example/xbookmarkbrain?sslmode=require&channel_binding=require",
         "postgresql://user:p%40ss@db.example/xbookmarkbrain_test?sslmode=require&channel_binding=require"),
    ],
)
def test_replace_database_name_preserves_url_components(dsn, expected):
    assert storage.replace_database_name(dsn) == expected


def test_database_identity_is_normalized_and_ignores_credentials_and_query():
    first = "postgresql://one:secret@DB.EXAMPLE/same?sslmode=require"
    second = "postgres://two:other@db.example:5432/same?sslmode=disable"
    assert storage.database_identity(first) == storage.database_identity(second)
    with pytest.raises(RuntimeError, match="development and test DSNs match"):
        storage.assert_distinct_database_urls(first, second)


def test_distinct_database_names_pass_identity_guard():
    development = "postgresql://user:pass@localhost/xbookmarkbrain"
    test = storage.replace_database_name(development)
    storage.assert_distinct_database_urls(development, test)
