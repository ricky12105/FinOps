import importlib.util
import inspect
import sys
import unittest
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace


def load_function_module():
    fabric = ModuleType("fabric")
    functions = ModuleType("fabric.functions")

    class UserDataFunctions:
        def connection(self, **kwargs):
            return lambda function: function

        def context(self, **kwargs):
            return lambda function: function

        def function(self):
            return lambda function: function

    class UserThrownError(Exception):
        pass

    functions.UserDataFunctions = UserDataFunctions
    functions.UserThrownError = UserThrownError
    functions.FabricSqlConnection = type("FabricSqlConnection", (), {})
    functions.UserDataFunctionContext = type("UserDataFunctionContext", (), {})
    fabric.functions = functions
    sys.modules["fabric"] = fabric
    sys.modules["fabric.functions"] = functions

    path = Path(__file__).parents[1] / "business-context" / "function.py"
    spec = importlib.util.spec_from_file_location("business_context_function", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


function_module = load_function_module()


class BusinessContextTests(unittest.TestCase):
    def test_request_hash_is_stable_and_tracks_assignment_semantics(self):
        first = function_module._request_hash("resource-1", "12345678-1234-5678-1234-567812345678", "2026-10-09", "Owner approved")
        same = function_module._request_hash("resource-1", "12345678-1234-5678-1234-567812345678", "2026-10-09", "Owner approved")
        different_reason = function_module._request_hash("resource-1", "12345678-1234-5678-1234-567812345678", "2026-10-09", "Revised approved owner")
        self.assertEqual(first, same)
        self.assertNotEqual(first, different_reason)
        self.assertEqual(len(first), 64)

    def test_uuid_is_canonicalized(self):
        self.assertEqual(
            function_module._uuid("12345678123456781234567812345678", "Actor"),
            "12345678-1234-5678-1234-567812345678",
        )

    def test_invalid_uuid_is_rejected(self):
        with self.assertRaises(function_module.fn.UserThrownError):
            function_module._uuid("caller@example.com", "Actor")

    def test_verified_invocation_identity_is_used_for_write(self):
        class Cursor:
            def __init__(self):
                self.parameters = None

            def execute(self, query, *parameters):
                self.parameters = parameters

            def fetchone(self):
                return ("saved", 2)

            def close(self):
                pass

        class Connection:
            def __init__(self):
                self.cursor_value = Cursor()
                self.committed = False

            def cursor(self):
                return self.cursor_value

            def commit(self):
                self.committed = True

            def rollback(self):
                raise AssertionError("successful assignment should not roll back")

            def close(self):
                pass

        connection = Connection()
        context = SimpleNamespace(
            executing_user={
                "Oid": "12345678-1234-5678-1234-567812345678",
                "TenantId": "87654321-4321-8765-4321-876543218765",
            },
            invocation_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        )
        result = function_module.assign_business_context(
            SimpleNamespace(connect=lambda: connection),
            context,
            "resource-key",
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "2026-10-09",
            "Approved ownership mapping",
            1,
        )
        self.assertEqual(result, "Business context assigned.")
        self.assertEqual(connection.cursor_value.parameters[0], "87654321-4321-8765-4321-876543218765")
        self.assertEqual(connection.cursor_value.parameters[1], "12345678-1234-5678-1234-567812345678")
        self.assertNotEqual(connection.cursor_value.parameters[2], "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        self.assertEqual(str(function_module.uuid.UUID(connection.cursor_value.parameters[2])), connection.cursor_value.parameters[2])
        self.assertTrue(connection.committed)
        self.assertNotIn("actorObjectId", inspect.signature(function_module.assign_business_context).parameters)

    def test_denied_assignment_rolls_back_and_is_not_reported_as_success(self):
        class Cursor:
            def execute(self, query, *parameters):
                pass

            def fetchone(self):
                return ("not_authorized", 1)

            def close(self):
                pass

        class Connection:
            def __init__(self):
                self.rolled_back = False

            def cursor(self):
                return Cursor()

            def commit(self):
                raise AssertionError("denied assignment must not commit")

            def rollback(self):
                self.rolled_back = True

            def close(self):
                pass

        connection = Connection()
        context = SimpleNamespace(
            executing_user={"Oid": "12345678-1234-5678-1234-567812345678", "TenantId": "87654321-4321-8765-4321-876543218765"},
            invocation_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        )
        with self.assertRaises(function_module.fn.UserThrownError):
            function_module.assign_business_context(
                SimpleNamespace(connect=lambda: connection),
                context,
                "resource-key",
                "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "2026-10-09",
                "Unauthorized mapping request",
                1,
            )
        self.assertTrue(connection.rolled_back)


if __name__ == "__main__":
    unittest.main()