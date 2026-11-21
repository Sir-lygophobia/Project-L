import unittest
from types import SimpleNamespace

from Commands.on_interaction import (
    ticket_owner_id,
    ticket_permission_overwrites,
    ticket_staff_overwrites,
)


class HashableObject:
    pass


class FakeRole(HashableObject):
    def __init__(self, *, default=False, administrator=False, manage_channels=False):
        self._default = default
        self.permissions = SimpleNamespace(
            administrator=administrator,
            manage_channels=manage_channels,
        )

    def is_default(self):
        return self._default


class TicketHelperTests(unittest.TestCase):
    def test_ticket_owner_topic_parsing_is_exact(self):
        self.assertEqual(
            ticket_owner_id(SimpleNamespace(topic="ticket-owner:123")), 123
        )
        for topic in (None, "ticket-owner:123-extra", "prefix-ticket-owner:123"):
            with self.subTest(topic=topic):
                self.assertIsNone(ticket_owner_id(SimpleNamespace(topic=topic)))

    def test_ticket_overwrites_are_private_and_allow_owner_staff_and_bot(self):
        everyone = FakeRole(default=True)
        verified = FakeRole()
        staff = FakeRole(manage_channels=True)
        administrator = FakeRole(administrator=True)
        owner = HashableObject()
        bot_member = HashableObject()
        guild = SimpleNamespace(
            default_role=everyone,
            roles=[everyone, verified, staff, administrator],
            me=bot_member,
        )
        category = SimpleNamespace(guild=guild, overwrites={})

        overwrites = ticket_permission_overwrites(category, owner)

        self.assertFalse(overwrites[everyone].view_channel)
        self.assertTrue(overwrites[owner].view_channel)
        self.assertTrue(overwrites[owner].send_messages)
        self.assertTrue(overwrites[staff].view_channel)
        self.assertTrue(overwrites[administrator].view_channel)
        self.assertTrue(overwrites[bot_member].manage_channels)
        self.assertNotIn(verified, overwrites)

        staff_only = ticket_staff_overwrites(category)
        self.assertFalse(staff_only[everyone].view_channel)
        self.assertTrue(staff_only[staff].view_channel)
        self.assertTrue(staff_only[bot_member].view_channel)
        self.assertNotIn(owner, staff_only)


if __name__ == "__main__":
    unittest.main()
