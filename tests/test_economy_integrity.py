import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from Commands.economy import (
    credit_balance,
    debit_balance_to_zero,
    ensure_economy_users,
    rob_balance_transfer,
    settle_wager,
    transfer_balance,
)


class EconomyIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "users.db"
        with closing(sqlite3.connect(self.database)) as conn:
            conn.execute(
                "CREATE TABLE users ("
                "userid INTEGER PRIMARY KEY, "
                "balance INTEGER, "
                "invited INTEGER, "
                "left INTEGER, "
                "voice INTEGER DEFAULT 0"
                ")"
            )
            conn.commit()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def add_user(self, user_id, balance, *, invited=0, left=0):
        with closing(sqlite3.connect(self.database)) as conn:
            conn.execute(
                "INSERT INTO users(userid, balance, invited, left) "
                "VALUES(?, ?, ?, ?)",
                (user_id, balance, invited, left),
            )
            conn.commit()

    def user(self, user_id):
        with closing(sqlite3.connect(self.database)) as conn:
            return conn.execute(
                "SELECT balance, invited, left FROM users WHERE userid = ?",
                (user_id,),
            ).fetchone()

    def test_credit_creates_with_full_amount_and_preserves_existing_metadata(self):
        self.assertEqual(credit_balance(1, 25, path=self.database), 25)
        self.assertEqual(self.user(1), (25, 0, 0))

        self.add_user(2, 10, invited=7, left=8)
        self.assertEqual(credit_balance(2, 15, path=self.database), 25)
        self.assertEqual(self.user(2), (25, 7, 8))

    def test_debit_creates_zero_balance_and_clamps_existing_balance(self):
        balance, created = debit_balance_to_zero(1, 40, path=self.database)
        self.assertEqual((balance, created), (0, True))
        self.assertEqual(self.user(1), (0, 0, 0))

        self.add_user(2, 100)
        self.assertEqual(
            debit_balance_to_zero(2, 30, path=self.database), (70, False)
        )
        self.assertEqual(
            debit_balance_to_zero(2, 500, path=self.database), (0, False)
        )

    def test_ensure_users_deduplicates_without_overwriting_existing_rows(self):
        self.add_user(1, 90, invited=3, left=4)

        ensure_economy_users((1, 1, 2), path=self.database)

        self.assertEqual(self.user(1), (90, 3, 4))
        self.assertEqual(self.user(2), (0, 0, 0))

    def test_transfer_is_conditional_and_creates_recipient(self):
        self.add_user(1, 100)

        self.assertTrue(transfer_balance(1, 2, 30, path=self.database))
        self.assertEqual(self.user(1)[0], 70)
        self.assertEqual(self.user(2)[0], 30)

        self.assertFalse(transfer_balance(1, 2, 80, path=self.database))
        self.assertEqual(self.user(1)[0], 70)
        self.assertEqual(self.user(2)[0], 30)

    def test_self_transfer_succeeds_without_changing_balance(self):
        self.add_user(1, 100)

        self.assertTrue(transfer_balance(1, 1, 40, path=self.database))

        self.assertEqual(self.user(1)[0], 100)

    def test_transfer_rolls_back_sender_debit_when_recipient_credit_fails(self):
        self.add_user(1, 100)
        self.add_user(2, 20)
        with closing(sqlite3.connect(self.database)) as conn:
            conn.execute(
                "CREATE TRIGGER reject_recipient_credit "
                "BEFORE UPDATE OF balance ON users "
                "WHEN OLD.userid = 2 "
                "BEGIN "
                "SELECT RAISE(ABORT, 'recipient blocked'); "
                "END"
            )
            conn.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            transfer_balance(1, 2, 30, path=self.database)

        self.assertEqual(self.user(1)[0], 100)
        self.assertEqual(self.user(2)[0], 20)

    def test_wager_requires_stake_and_applies_one_atomic_delta(self):
        self.assertFalse(settle_wager(1, 10, -10, path=self.database))
        self.assertEqual(self.user(1)[0], 0)

        self.add_user(2, 100)
        self.assertTrue(settle_wager(2, 25, -25, path=self.database))
        self.assertEqual(self.user(2)[0], 75)
        self.assertTrue(settle_wager(2, 25, 25, path=self.database))
        self.assertEqual(self.user(2)[0], 100)

    def test_rob_transfer_caps_amount_at_current_victim_balance(self):
        self.add_user(1, 5)
        self.add_user(2, 12)

        self.assertEqual(rob_balance_transfer(1, 2, 30, path=self.database), 12)
        self.assertEqual(self.user(1)[0], 17)
        self.assertEqual(self.user(2)[0], 0)
        self.assertEqual(rob_balance_transfer(1, 2, 30, path=self.database), 0)


if __name__ == "__main__":
    unittest.main()
