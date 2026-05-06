"""tests/test_record_assistant_usage.py — Stop hook で transcript から
`assistant_usage` event を集める処理の TDD テスト (Issue #99 / v0.8.0)。

カバー範囲:
- main session transcript からの収集 (`source="main"`)
- per-subagent transcript からの収集 (`source="subagent"`)
- Issue #93 で確定した `subagent_type == ""` filter rule (orphan invocation 除外)
- `(session_id, message_id)` first-wins dedup (再発火 / 二重観測 idempotent)
- model 切替の per-message 記録
- service_tier / inference_geo の passthrough
- naive timestamp / 欠損 message_id の silent skip
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HOOKS_DIR = ROOT / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))


def _read_events(path: Path):
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, records: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _assistant_record(msg_id: str, ts: str, *,
                      model: str = "claude-sonnet-4-6",
                      input_tokens: int = 100, output_tokens: int = 50,
                      cache_read_input_tokens: int = 0,
                      cache_creation_input_tokens: int = 0,
                      service_tier: str | None = "<unset>",
                      inference_geo: str | None = "<unset>"):
    """assistant role transcript record factory.

    `service_tier="<unset>"` / `inference_geo="<unset>"` で field 自体を省略する
    sentinel。`None` を渡すと `null` として実 transcript の null 値を表現できる。
    """
    usage: dict = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
    }
    if service_tier != "<unset>":
        usage["service_tier"] = service_tier
    if inference_geo != "<unset>":
        usage["inference_geo"] = inference_geo
    return {
        "timestamp": ts,
        "message": {
            "role": "assistant",
            "id": msg_id,
            "model": model,
            "usage": usage,
            "content": [],
        },
    }


def _user_record(ts: str):
    return {"timestamp": ts, "message": {"role": "user", "content": "hi"}}


def _stop_event(*, session_id: str, agent_id: str, agent_type: str,
                ts: str = "2026-05-01T10:01:30+00:00"):
    return {
        "event_type": "subagent_stop",
        "session_id": session_id,
        "subagent_id": agent_id,
        "subagent_type": agent_type,
        "timestamp": ts,
    }


class _BaseFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.usage_file = self.tmpdir / "usage.jsonl"
        self.transcript = self.tmpdir / "transcript.jsonl"
        self.session_id = "s1"
        self.cwd = "/tmp/myproj"

    def tearDown(self):
        self._tmp.cleanup()

    def make_payload(self, **overrides):
        return {
            "hook_event_name": "Stop",
            "session_id": overrides.get("session_id", self.session_id),
            "cwd": overrides.get("cwd", self.cwd),
            "transcript_path": str(overrides.get("transcript_path", self.transcript)),
        }

    def run_hook(self, payload):
        from record_assistant_usage import handle_stop
        handle_stop(payload, data_file=self.usage_file)

    def assistant_usage_events(self):
        return [e for e in _read_events(self.usage_file)
                if e.get("event_type") == "assistant_usage"]


class TestStopHookEmitsAssistantUsage(_BaseFixture):
    def test_main_transcript_assistant_message_recorded(self):
        _write_jsonl(self.transcript, [
            _user_record("2026-05-01T10:00:00.000Z"),
            _assistant_record("msg_1", "2026-05-01T10:00:01.000Z",
                              input_tokens=1234, output_tokens=567,
                              cache_read_input_tokens=89,
                              cache_creation_input_tokens=10),
        ])
        self.run_hook(self.make_payload())
        au = self.assistant_usage_events()
        self.assertEqual(len(au), 1)
        ev = au[0]
        self.assertEqual(ev["source"], "main")
        self.assertEqual(ev["session_id"], "s1")
        self.assertEqual(ev["project"], "myproj")
        self.assertEqual(ev["message_id"], "msg_1")
        self.assertEqual(ev["model"], "claude-sonnet-4-6")
        self.assertEqual(ev["input_tokens"], 1234)
        self.assertEqual(ev["output_tokens"], 567)
        self.assertEqual(ev["cache_read_tokens"], 89)
        self.assertEqual(ev["cache_creation_tokens"], 10)

    def test_user_records_ignored(self):
        _write_jsonl(self.transcript, [
            _user_record("2026-05-01T10:00:00.000Z"),
            _user_record("2026-05-01T10:00:01.000Z"),
        ])
        self.run_hook(self.make_payload())
        self.assertEqual(self.assistant_usage_events(), [])

    def test_assistant_without_usage_skipped(self):
        # message.usage 欠損の record (cancelled / streaming 不完全) は drop
        _write_jsonl(self.transcript, [
            _assistant_record("msg_ok", "2026-05-01T10:00:01.000Z"),
            {"timestamp": "2026-05-01T10:00:02.000Z",
             "message": {"role": "assistant", "id": "msg_no_usage",
                         "model": "claude-sonnet-4-6", "content": []}},
        ])
        self.run_hook(self.make_payload())
        au = self.assistant_usage_events()
        self.assertEqual(len(au), 1)
        self.assertEqual(au[0]["message_id"], "msg_ok")

    def test_missing_transcript_no_emit(self):
        # transcript_path が存在しない (Stop 時にまだ flush されてない / pristine session)
        # → silent skip、usage.jsonl を作らない / エラー出さない
        payload = self.make_payload(transcript_path=self.tmpdir / "does_not_exist.jsonl")
        self.run_hook(payload)
        self.assertEqual(self.assistant_usage_events(), [])


class TestSubagentTranscriptCollected(_BaseFixture):
    def test_per_subagent_transcript_picked_up(self):
        _write_jsonl(self.transcript, [_user_record("2026-05-01T10:00:00.000Z")])
        sa_dir = self.transcript.with_suffix("") / "subagents"
        sa_file = sa_dir / "agent-AGENT_1.jsonl"
        _write_jsonl(sa_file, [
            _assistant_record("msg_sa_1", "2026-05-01T10:01:00.000Z",
                              model="claude-haiku-4-5"),
        ])
        # 有効な subagent_stop (Issue #93 filter rule pass) を usage.jsonl に事前注入
        _write_jsonl(self.usage_file, [
            _stop_event(session_id="s1", agent_id="AGENT_1", agent_type="Explore"),
        ])
        self.run_hook(self.make_payload())
        au = self.assistant_usage_events()
        self.assertEqual(len(au), 1)
        self.assertEqual(au[0]["source"], "subagent")
        self.assertEqual(au[0]["message_id"], "msg_sa_1")
        self.assertEqual(au[0]["model"], "claude-haiku-4-5")

    def test_multiple_subagent_transcripts_processed(self):
        _write_jsonl(self.transcript, [_user_record("2026-05-01T10:00:00.000Z")])
        sa_dir = self.transcript.with_suffix("") / "subagents"
        _write_jsonl(sa_dir / "agent-A1.jsonl",
                     [_assistant_record("msg_a", "2026-05-01T10:01:00.000Z")])
        _write_jsonl(sa_dir / "agent-A2.jsonl",
                     [_assistant_record("msg_b", "2026-05-01T10:02:00.000Z")])
        _write_jsonl(self.usage_file, [
            _stop_event(session_id="s1", agent_id="A1", agent_type="Explore"),
            _stop_event(session_id="s1", agent_id="A2", agent_type="general-purpose"),
        ])
        self.run_hook(self.make_payload())
        au = self.assistant_usage_events()
        self.assertEqual(
            sorted(e["message_id"] for e in au),
            ["msg_a", "msg_b"],
        )


class TestSubagentTypeFilterRule(_BaseFixture):
    """Issue #93: subagent_type == "" は orphan / メインスレッド誤発火。除外する。"""

    def test_orphan_subagent_transcript_skipped(self):
        _write_jsonl(self.transcript, [_user_record("2026-05-01T10:00:00.000Z")])
        sa_dir = self.transcript.with_suffix("") / "subagents"
        _write_jsonl(sa_dir / "agent-ORPHAN.jsonl",
                     [_assistant_record("msg_orphan", "2026-05-01T10:01:00.000Z")])
        _write_jsonl(self.usage_file, [
            _stop_event(session_id="s1", agent_id="ORPHAN", agent_type=""),  # 空 type
        ])
        self.run_hook(self.make_payload())
        self.assertEqual(self.assistant_usage_events(), [])

    def test_no_subagent_stop_means_skip(self):
        # transcript file は存在するが subagent_stop 観測なし → 安全側で skip
        _write_jsonl(self.transcript, [_user_record("2026-05-01T10:00:00.000Z")])
        sa_dir = self.transcript.with_suffix("") / "subagents"
        _write_jsonl(sa_dir / "agent-UNKNOWN.jsonl",
                     [_assistant_record("msg_u", "2026-05-01T10:01:00.000Z")])
        # usage.jsonl 空
        self.run_hook(self.make_payload())
        self.assertEqual(self.assistant_usage_events(), [])


class TestDedupByMessageId(_BaseFixture):
    def test_double_invocation_no_duplicate(self):
        _write_jsonl(self.transcript, [
            _assistant_record("msg_x", "2026-05-01T10:00:01.000Z"),
        ])
        self.run_hook(self.make_payload())
        self.run_hook(self.make_payload())
        self.assertEqual(len(self.assistant_usage_events()), 1)

    def test_main_and_subagent_share_message_id_first_wins(self):
        _write_jsonl(self.transcript, [
            _assistant_record("msg_shared", "2026-05-01T10:00:00.000Z"),
        ])
        sa_dir = self.transcript.with_suffix("") / "subagents"
        _write_jsonl(sa_dir / "agent-X.jsonl",
                     [_assistant_record("msg_shared", "2026-05-01T10:00:00.000Z")])
        _write_jsonl(self.usage_file, [
            _stop_event(session_id="s1", agent_id="X", agent_type="Explore"),
        ])
        self.run_hook(self.make_payload())
        au = self.assistant_usage_events()
        self.assertEqual(len(au), 1)
        # main 経路を先に処理 → source="main" が first-wins
        self.assertEqual(au[0]["source"], "main")


class TestModelSwitchInSession(_BaseFixture):
    def test_per_message_model_pinned(self):
        _write_jsonl(self.transcript, [
            _assistant_record("m1", "2026-05-01T10:00:00.000Z", model="claude-opus-4-7"),
            _assistant_record("m2", "2026-05-01T10:01:00.000Z", model="claude-haiku-4-5"),
            _assistant_record("m3", "2026-05-01T10:02:00.000Z", model="claude-haiku-4-5"),
        ])
        self.run_hook(self.make_payload())
        au = sorted(self.assistant_usage_events(), key=lambda e: e["message_id"])
        self.assertEqual(len(au), 3)
        self.assertEqual(
            [e["model"] for e in au],
            ["claude-opus-4-7", "claude-haiku-4-5", "claude-haiku-4-5"],
        )


class TestServiceTierCaptured(_BaseFixture):
    def test_service_tier_passthrough(self):
        _write_jsonl(self.transcript, [
            _assistant_record("m1", "2026-05-01T10:00:00.000Z", service_tier="priority"),
            _assistant_record("m2", "2026-05-01T10:01:00.000Z", service_tier="standard"),
            _assistant_record("m3", "2026-05-01T10:02:00.000Z"),  # 欠損
        ])
        self.run_hook(self.make_payload())
        au = sorted(self.assistant_usage_events(), key=lambda e: e["message_id"])
        self.assertEqual(au[0]["service_tier"], "priority")
        self.assertEqual(au[1]["service_tier"], "standard")
        self.assertIsNone(au[2]["service_tier"])

    def test_inference_geo_passthrough(self):
        _write_jsonl(self.transcript, [
            _assistant_record("m1", "2026-05-01T10:00:00.000Z", inference_geo="us-east"),
            _assistant_record("m2", "2026-05-01T10:00:01.000Z"),
        ])
        self.run_hook(self.make_payload())
        au = sorted(self.assistant_usage_events(), key=lambda e: e["message_id"])
        self.assertEqual(au[0]["inference_geo"], "us-east")
        self.assertIsNone(au[1]["inference_geo"])


class TestMissingMessageIdSkipped(_BaseFixture):
    def test_record_without_msg_id_dropped(self):
        _write_jsonl(self.transcript, [
            {"timestamp": "2026-05-01T10:00:00.000Z",
             "message": {"role": "assistant", "model": "claude-sonnet-4-6",
                         "usage": {"input_tokens": 100, "output_tokens": 50},
                         "content": []}},  # no "id"
            _assistant_record("m_ok", "2026-05-01T10:00:01.000Z"),
        ])
        self.run_hook(self.make_payload())
        au = self.assistant_usage_events()
        self.assertEqual(len(au), 1)
        self.assertEqual(au[0]["message_id"], "m_ok")


class TestNaiveTimestampHandled(_BaseFixture):
    def test_naive_timestamp_dropped(self):
        # spec §Step 1: naive timestamp は drop (silent)
        _write_jsonl(self.transcript, [
            {"timestamp": "2026-05-01T10:00:00",  # no Z, no offset
             "message": {"role": "assistant", "id": "m_naive",
                         "model": "claude-sonnet-4-6",
                         "usage": {"input_tokens": 1, "output_tokens": 1},
                         "content": []}},
            _assistant_record("m_ok", "2026-05-01T10:00:01.000Z"),
        ])
        self.run_hook(self.make_payload())
        au = self.assistant_usage_events()
        self.assertEqual(len(au), 1)
        self.assertEqual(au[0]["message_id"], "m_ok")

    def test_unparseable_timestamp_dropped(self):
        _write_jsonl(self.transcript, [
            {"timestamp": "not-a-date",
             "message": {"role": "assistant", "id": "m_bad_ts",
                         "model": "claude-sonnet-4-6",
                         "usage": {"input_tokens": 1, "output_tokens": 1},
                         "content": []}},
            _assistant_record("m_ok", "2026-05-01T10:00:00.000Z"),
        ])
        self.run_hook(self.make_payload())
        au = self.assistant_usage_events()
        self.assertEqual([e["message_id"] for e in au], ["m_ok"])


if __name__ == "__main__":
    unittest.main()
