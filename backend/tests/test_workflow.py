"""AB实验 / ETL任务执行链验收测试。

覆盖：
- 两种动作请求（顶层 action、嵌套 values.action）都生效且结论一致；
- 冲突输入拒绝、缺动作拒绝，不随机选择；
- 状态只能单步向前：跳级、回退、终态再流转（含失败终态再归档）全部拒绝；
- 相同操作重试幂等，不改写状态；
- 完成与终止并发竞争只接受一条结论，失败请求不改写已有结果；
- 操作台账同步记录接受/幂等/拒绝及原因；列表与详情结论一致；
- 历史数据迁移不把运行中样例当成重新启动的草稿，终态标记被校正。

仅依赖标准库 unittest 与 fastapi TestClient，直接运行：
    PYTHONPATH=... python3 -m unittest -v tests.test_workflow
"""
from __future__ import annotations

import threading
import unittest

from fastapi.testclient import TestClient

from app.main import app


class WorkflowTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def _create(self, endpoint: str, number: str) -> int:
        key = "方案编号" if endpoint == "/api/abtest" else "任务编号"
        resp = self.client.post(endpoint, json={"values": {key: number, "实验名称": number,
                                                          "对照组配置": number,
                                                          "输入源": number, "输出目标": number}})
        self.assertTrue(resp.json()["ok"], resp.text)
        return resp.json()["entry"]["id"]

    def _action(self, endpoint: str, entry_id: int, body: dict) -> dict:
        return self.client.post(f"{endpoint}/{entry_id}/actions", json=body).json()

    def _status(self, endpoint: str, entry_id: int) -> str:
        return self.client.get(f"{endpoint}/{entry_id}").json()["status"]

    # ---------- 协议兼容 ----------

    def test_01_two_protocols_both_work_and_agree(self) -> None:
        for endpoint, first_action in [
            ("/api/abtest", "提交审批"),
            ("/api/etljob", "提交调度"),
        ]:
            with self.subTest(endpoint=endpoint):
                eid = self._create(endpoint, f"P-{endpoint}")
                top = self._action(endpoint, eid, {"action": first_action})
                nested = self._create(endpoint, f"Q-{endpoint}")
                nes = self._action(endpoint, nested, {"values": {"action": first_action}})
                self.assertTrue(top["ok"])
                self.assertTrue(nes["ok"])
                self.assertEqual(self._status(endpoint, eid), self._status(endpoint, nested))

    def test_02_conflicting_inputs_are_rejected(self) -> None:
        for endpoint in ["/api/abtest", "/api/etljob"]:
            eid = self._create(endpoint, f"C-{endpoint}")
            before = self._status(endpoint, eid)
            result = self._action(endpoint, eid, {"action": "启动实验",
                                                  "values": {"action": "提交审批"}})
            self.assertFalse(result["ok"])
            self.assertIn("冲突", result["message"])
            self.assertEqual(self._status(endpoint, eid), before)  # 冲突不允许改写状态

    def test_03_missing_action_rejected(self) -> None:
        for endpoint in ["/api/abtest", "/api/etljob"]:
            eid = self._create(endpoint, f"M-{endpoint}")
            result = self._action(endpoint, eid, {"values": {}})
            self.assertFalse(result["ok"])
            self.assertIn("缺少动作", result["message"])

    def test_04_duplicate_same_action_is_idempotent(self) -> None:
        eid = self._create("/api/abtest", "IDEM-AB")
        first = self._action("/api/abtest", eid, {"action": "提交审批"})
        again = self._action("/api/abtest", eid, {"values": {"action": "提交审批"}})
        self.assertTrue(first["ok"])
        self.assertTrue(again["ok"])
        self.assertTrue(again["idempotent"])
        self.assertEqual(self._status("/api/abtest", eid), "待审批")

    # ---------- 顺序约束：跳级 / 回退 / 终态 ----------

    def test_05_skip_level_and_backward_are_rejected(self) -> None:
        # 草稿直接启动（跳级）
        eid = self._create("/api/abtest", "SKIP-AB")
        skip = self._action("/api/abtest", eid, {"action": "启动实验"})
        self.assertFalse(skip["ok"])
        self.assertIn("禁止跳级或回退", skip["message"])
        self.assertEqual(self._status("/api/abtest", eid), "草稿")

        # 走到待审批后再提交审批（回退/重复非目标态）与回草稿
        self._action("/api/abtest", eid, {"action": "提交审批"})
        backward = self._action("/api/abtest", eid, {"values": {"action": "提交审批"}})
        self.assertTrue(backward["idempotent"])  # 目标态相同 => 幂等而非再次流转
        self.assertEqual(self._status("/api/abtest", eid), "待审批")

        # 待审批直接归档（跳两级到终态）
        jump = self._action("/api/abtest", eid, {"action": "归档实验"})
        self.assertFalse(jump["ok"])
        self.assertIn("禁止跳级或回退", jump["message"])

    def test_06_terminal_states_cannot_transition_again(self) -> None:
        # AB：运行中 -> 已终止，之后跨动作必须拒绝；重复同一终态动作为幂等
        eid = self._create("/api/abtest", "TERM-AB")
        self._action("/api/abtest", eid, {"action": "提交审批"})
        self._action("/api/abtest", eid, {"action": "启动实验"})
        self._action("/api/abtest", eid, {"action": "停止实验"})
        self.assertEqual(self._status("/api/abtest", eid), "已终止")
        # 终态上发起“另一条结论”（归档/再启动）必须拒绝
        for action in ["归档实验", "启动实验"]:
            rejected = self._action("/api/abtest", eid, {"action": action})
            self.assertFalse(rejected["ok"], action)
            self.assertIn("终态", rejected["message"])
        # 重复同一个停止动作是幂等重试，不得二次改写
        retry = self._action("/api/abtest", eid, {"action": "停止实验"})
        self.assertTrue(retry["ok"])
        self.assertTrue(retry["idempotent"])
        self.assertEqual(self._status("/api/abtest", eid), "已终止")

        # ETL：运行中 -> 已失败（负向终态），再归档成功必须拒绝；重复终止为幂等
        job = self._create("/api/etljob", "TERM-ETL")
        self._action("/api/etljob", job, {"action": "提交调度"})
        self._action("/api/etljob", job, {"action": "启动任务"})
        self._action("/api/etljob", job, {"action": "终止任务"})
        self.assertEqual(self._status("/api/etljob", job), "已失败")
        re_archive = self._action("/api/etljob", job, {"values": {"action": "归档结果"}})
        self.assertFalse(re_archive["ok"])
        self.assertIn("终态", re_archive["message"])
        retry_fail = self._action("/api/etljob", job, {"action": "终止任务"})
        self.assertTrue(retry_fail["ok"])
        self.assertTrue(retry_fail["idempotent"])
        self.assertEqual(self._status("/api/etljob", job), "已失败")

    def test_07_full_legal_happy_path(self) -> None:
        # AB 正常归档路径
        eid = self._create("/api/abtest", "HAPPY-AB")
        for action, expect in [("提交审批", "待审批"), ("启动实验", "运行中"),
                               ("归档实验", "已完成")]:
            r = self._action("/api/abtest", eid, {"action": action})
            self.assertTrue(r["ok"], r["message"])
            self.assertEqual(self._status("/api/abtest", eid), expect)
        detail = self.client.get(f"/api/abtest/{eid}").json()
        self.assertFalse(detail["pending"])
        self.assertFalse(detail["abnormal"])

        # ETL 正常完成路径
        job = self._create("/api/etljob", "HAPPY-ETL")
        for action, expect in [("提交调度", "待调度"), ("启动任务", "运行中"),
                               ("归档结果", "已完成")]:
            r = self._action("/api/etljob", job, {"values": {"action": action}})
            self.assertTrue(r["ok"], r["message"])
            self.assertEqual(self._status("/api/etljob", job), expect)

    # ---------- 并发竞争 ----------

    def test_08_terminal_race_only_one_conclusion_wins(self) -> None:
        for endpoint, done_action, stop_action in [
            ("/api/abtest", "归档实验", "停止实验"),
            ("/api/etljob", "归档结果", "终止任务"),
        ]:
            with self.subTest(endpoint=endpoint):
                eid = self._create(endpoint, f"RACE-{endpoint}")
                # 推进到运行中
                for act in (["提交审批", "启动实验"] if endpoint == "/api/abtest"
                            else ["提交调度", "启动任务"]):
                    self.assertTrue(self._action(endpoint, eid, {"action": act})["ok"])

                barrier = threading.Barrier(2)
                results: list[dict] = []

                def fire(action: str) -> None:
                    barrier.wait()
                    results.append(self._action(endpoint, eid, {"action": action}))

                t1 = threading.Thread(target=fire, args=(done_action,))
                t2 = threading.Thread(target=fire, args=(stop_action,))
                t1.start(); t2.start(); t1.join(); t2.join()

                accepted = [r for r in results if r["ok"] and not r["idempotent"]]
                rejected = [r for r in results if not r["ok"]]
                self.assertEqual(len(accepted), 1, results)
                self.assertEqual(len(rejected), 1, results)
                final = self._status(endpoint, eid)
                self.assertIn(final, ["已完成", "已终止", "已失败"])
                # 被拒绝的请求不能改写已接受的结论：详情与列表口径一致
                listed = [r for r in self.client.get(endpoint).json()["items"]
                          if r["id"] == eid][0]
                self.assertEqual(listed["status"], final)
                self.assertIn("终态", rejected[0]["message"])

    # ---------- 台账 ----------

    def test_09_ledger_records_accepted_idempotent_and_rejected(self) -> None:
        eid = self._create("/api/abtest", "LEDGER-AB")
        self._action("/api/abtest", eid, {"action": "提交审批"})          # 接受
        self._action("/api/abtest", eid, {"action": "提交审批"})          # 幂等
        self._action("/api/abtest", eid, {"action": "归档实验"})          # 拒绝（跳级）
        ledger = self.client.get(f"/api/abtest/{eid}/ledger").json()["items"]
        kinds = [(x["accepted"], x["idempotent"]) for x in ledger]
        self.assertIn((True, False), kinds)
        self.assertIn((False, True), kinds)
        self.assertIn((False, False), kinds)
        rejected = [x for x in ledger if not x["accepted"] and not x["idempotent"]][0]
        self.assertTrue(rejected["reason"])
        # 接受记录的 from/to 必须体现真实推进
        accepted = [x for x in ledger if x["accepted"]][0]
        self.assertEqual((accepted["from_status"], accepted["to_status"]), ("草稿", "待审批"))

    def test_10_ledger_not_found_returns_404(self) -> None:
        resp = self.client.get("/api/abtest/999999/ledger")
        self.assertEqual(resp.status_code, 404)

    # ---------- 历史数据迁移 ----------

    def test_11_legacy_running_sample_not_treated_as_draft(self) -> None:
        # 种子 id=3 在两个模块都是「运行中」，迁移不得改变其真实状态。
        ab3 = self.client.get("/api/abtest/3").json()
        etl3 = self.client.get("/api/etljob/3").json()
        self.assertEqual(ab3["status"], "运行中")
        self.assertEqual(etl3["status"], "运行中")
        # 运行中不是终态 => pending 保持 True；abnormal 仍为 False
        self.assertTrue(ab3["pending"]); self.assertFalse(ab3["abnormal"])
        self.assertTrue(etl3["pending"]); self.assertFalse(etl3["abnormal"])
        # 运行中的历史样例不能被“提交审批/调度”（那是草稿动作）回退
        reject = self._action("/api/abtest", 3, {"action": "提交审批"})
        self.assertFalse(reject["ok"])
        self.assertEqual(self._status("/api/abtest", 3), "运行中")

    def test_12_http_200_business_rejection_carries_reason(self) -> None:
        eid = self._create("/api/etljob", "HTTP200")
        resp = self.client.post(f"/api/etljob/{eid}/actions", json={"action": "终止任务"})
        self.assertEqual(resp.status_code, 200)       # 传输成功
        body = resp.json()
        self.assertFalse(body["ok"])                  # 业务拒绝
        self.assertTrue(body["message"])             # 且能看到原因

    def test_13_export_route_not_shadowed_by_entry_id(self) -> None:
        # /export 必须声明在 /{entry_id} 之前，否则会被当成 entry_id 报 422。
        for endpoint in ["/api/abtest", "/api/etljob"]:
            resp = self.client.get(f"{endpoint}/export")
            self.assertEqual(resp.status_code, 200)
            self.assertIn("items", resp.json())

    def test_14_conflict_leaves_no_accepted_ledger_record(self) -> None:
        eid = self._create("/api/abtest", "CONFLICT-LEDGER")
        bad = self._action("/api/abtest", eid,
                           {"action": "启动实验", "values": {"action": "提交审批"}})
        self.assertFalse(bad["ok"])
        ledger = self.client.get(f"/api/abtest/{eid}/ledger").json()["items"]
        # 冲突在进入状态机前即被拒绝，台账不得出现任何“已接受”记录。
        self.assertFalse(any(x["accepted"] for x in ledger))
        self.assertEqual(self._status("/api/abtest", eid), "草稿")


if __name__ == "__main__":
    unittest.main(verbosity=2)
