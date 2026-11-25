import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import main


class TestCatalogAndPlans(unittest.TestCase):
    def test_load_catalog_from_plans_json(self):
        plans, groups = main.load_catalog()
        self.assertEqual(len(plans), 28)
        self.assertIn("pro_12m", plans)
        self.assertEqual(plans["pro_12m"]["sku"], "g1.2tb.ai.subs_variant")
        self.assertEqual(plans["pro_12m"]["offer"], "12months-eft")
        self.assertEqual([title for title, _ in groups], ["Plus", "Pro", "Ultra 5x", "Ultra 20x"])

    def test_parse_plans_fallback_matches_json(self):
        plans_json, _ = main.load_catalog()
        parsed = main.parse_plans(main.JS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(set(parsed.keys()), set(plans_json.keys()))

    def test_resolve_plan_by_id_and_index(self):
        plans, groups = main.load_catalog()
        grouped = main.grouped_plans(plans, groups_cfg=groups)
        index = {str(i): pid for i, (_t, pid, _s) in enumerate(main.flatten_menu(grouped), 1)}
        index.update({pid: pid for pid in plans})
        self.assertEqual(main.resolve_plan("pro_12m", plans, index), "pro_12m")
        self.assertEqual(main.resolve_plan("1", plans, index), "helium_12m")
        self.assertEqual(main.resolve_plan("nonexistent", plans, index), "")

    def test_resolve_plan_chain(self):
        plans, groups = main.load_catalog()
        grouped = main.grouped_plans(plans, groups_cfg=groups)
        index = {str(i): pid for i, (_t, pid, _s) in enumerate(main.flatten_menu(grouped), 1)}
        index.update({pid: pid for pid in plans})
        chain, bad = main.resolve_plan_chain("pro_12m, 15, pro_6m", plans, index)
        self.assertEqual(bad, "")
        self.assertEqual(chain, ["pro_12m", "pro_6m_2", "pro_6m"])
        chain2, bad2 = main.resolve_plan_chain("pro_12m, invalid_sku", plans, index)
        self.assertEqual(chain2, [])
        self.assertEqual(bad2, "invalid_sku")

    def test_default_cli_settings_neutral(self):
        with mock.patch("sys.argv", ["main.py"]):
            args = main.parse_args()
        self.assertEqual(args.plan, "")
        self.assertEqual(args.stack_plans, "")
        self.assertFalse(args.stack)
        self.assertEqual(args.addon, "")
        self.assertFalse(args.auto_remove)
        self.assertFalse(args.loop)


class TestAdbHelpers(unittest.TestCase):
    def test_dedupe_prefers_emulator_serial(self):
        a = main.Adb()
        with mock.patch.object(a, "_fingerprint", side_effect=["abc", "abc", "def"]):
            result = a._dedupe(["127.0.0.1:16384", "emulator-5554", "127.0.0.1:7555"])
        self.assertEqual(result, ["emulator-5554", "127.0.0.1:7555"])

    def test_db_has_g1_sub_active_and_expired(self):
        future_ms = "9999999999999"
        past_ms = "1000000000000"
        with mock.patch.object(main, "su_sh", return_value=f"subs:com.google.android.apps.subscriptions.red:g1.2tb.ai.subs_variant|{future_ms}"):
            self.assertTrue(main.db_has_g1_sub("user@gmail.com"))
        with mock.patch.object(main, "su_sh", return_value=f"subs:com.google.android.apps.subscriptions.red:g1.2tb.ai.subs_variant|{past_ms}"):
            self.assertFalse(main.db_has_g1_sub("user@gmail.com"))
        with mock.patch.object(main, "su_sh", return_value="subs:com.google.android.apps.subscriptions.red:g1.2tb.ai.subs_variant|"):
            self.assertTrue(main.db_has_g1_sub("user@gmail.com"))

    def test_account_play_country(self):
        xml = '<string name="account-play-countrytest@gmail.com">ID</string>'
        with mock.patch.object(main, "su_sh", return_value=xml):
            self.assertEqual(main.account_play_country("test@gmail.com"), "ID")
        with mock.patch.object(main, "su_sh", return_value=""):
            self.assertEqual(main.account_play_country("test@gmail.com"), "?")


class TestSessionStateAndLogging(unittest.TestCase):
    def test_session_purchase_and_detach_events(self):
        sess = main.Session()
        sess._on_message({"type": "send", "payload": {"ev": "pd", "code": 0, "n": 1}}, None)
        sess._on_message({"type": "send", "payload": {"ev": "launched", "code": 0}}, None)
        sess._on_message({"type": "send", "payload": {"ev": "purchase_cb", "code": 0, "n": 1}}, None)
        self.assertEqual(sess.pd_code, "0")
        self.assertEqual(sess.launch_code, "0")
        self.assertEqual(sess.wait_for_purchase(timeout=1.0), "0")

        sess2 = main.Session()
        sess2._on_detached("process-terminated")
        self.assertEqual(sess2.wait_for_purchase(timeout=1.0), "detached")

    def test_audit_logger_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "audit.jsonl"
            logger = main.AuditLogger(str(log_path))
            logger.write(account="a@gmail.com", plan="pro_12m", action="paid_and_removed")
            lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            row = json.loads(lines[0])
            self.assertEqual(row["account"], "a@gmail.com")
            self.assertEqual(row["action"], "paid_and_removed")
            self.assertIn("ts", row)


if __name__ == "__main__":
    unittest.main()
