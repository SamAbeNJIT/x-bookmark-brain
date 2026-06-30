"""Credits: prepaid balance is charged per ask, blocks at $0, never goes negative."""

from xbb import storage
from xbb.config import DEFAULT_TENANT_ID


def test_debit_blocks_when_insufficient(db):
    con = storage.connect(db)
    try:
        con.execute("UPDATE accounts SET credit_balance_usd = 0.15 WHERE id = %s",
                    (DEFAULT_TENANT_ID,)); con.commit()
        assert storage.debit_credits(con, 0.10) is True       # 0.15 -> 0.05
        assert abs(storage.credit_balance(con) - 0.05) < 1e-9
        assert storage.debit_credits(con, 0.10) is False      # can't cover; stays 0.05
        assert abs(storage.credit_balance(con) - 0.05) < 1e-9  # never went negative
    finally:
        con.close()


def test_add_and_refund_credits(db):
    con = storage.connect(db)
    try:
        con.execute("UPDATE accounts SET credit_balance_usd = 0 WHERE id = %s",
                    (DEFAULT_TENANT_ID,)); con.commit()
        storage.add_credits(con, DEFAULT_TENANT_ID, 10.0)
        assert storage.credit_balance(con) == 10.0
        storage.debit_credits(con, 0.10)
        storage.refund_credits(con, 0.10)
        assert abs(storage.credit_balance(con) - 10.0) < 1e-9
    finally:
        con.close()


def test_ask_route_blocks_when_out_of_credits(client, db):
    # Drain the funded default account, then the ask route should return the out-of-credits notice.
    con = storage.connect(db)
    try:
        con.execute("UPDATE accounts SET credit_balance_usd = 0 WHERE id = %s",
                    (DEFAULT_TENANT_ID,)); con.commit()
    finally:
        con.close()
    body = client.post("/ask", json={"question": "rag evaluation", "k": 3}).json()
    assert "out of credits" in body["answer"].lower()
    assert body["citations"] == []


def test_ask_route_works_and_debits_when_funded(client, db):
    con = storage.connect(db)
    try:
        con.execute("UPDATE accounts SET credit_balance_usd = 1.00 WHERE id = %s",
                    (DEFAULT_TENANT_ID,)); con.commit()
    finally:
        con.close()
    body = client.post("/ask", json={"question": "rag evaluation", "k": 3}).json()
    assert "out of credits" not in body["answer"].lower()    # a real answer
    con = storage.connect(db)
    try:
        assert abs(storage.credit_balance(con) - 0.90) < 1e-9  # charged one $0.10 ask
    finally:
        con.close()
