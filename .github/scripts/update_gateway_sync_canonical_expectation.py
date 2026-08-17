from pathlib import Path

path = Path("backend/tests/test_gateway_sync.py")
text = path.read_text(encoding="utf-8")
old = '''            assert result.status == "conflict"
            assert result.revision == server_revision
            assert result.server_snapshot["payload"] == {"content": "桌面版本"}

            conflict = db.query(SyncConflict).one()
            assert conflict.client_payload_json == {"content": "手机离线版本"}
            assert conflict.server_payload_json == {"content": "桌面版本"}
            state = (
                db.query(SyncEntityState)
                .filter(SyncEntityState.entity_type == "chapter")
                .one()
            )
            assert state.payload_json == {"content": "桌面版本"}
'''
new = '''            assert result.status == "conflict"
            assert result.revision == server_revision
            server_payload = result.server_snapshot["payload"]
            assert server_payload["_record_type"] == "chapter"
            assert server_payload["id"] == "chapter-1"
            assert server_payload["content"] == "桌面版本"
            assert server_payload["current_version"] == 1
            assert server_payload["word_count"] > 0

            conflict = db.query(SyncConflict).one()
            # Client branch keeps the exact stale request for conflict review;
            # server branch is the authoritative PC-shaped domain snapshot.
            assert conflict.client_payload_json == {"content": "手机离线版本"}
            assert conflict.server_payload_json == server_payload
            state = (
                db.query(SyncEntityState)
                .filter(SyncEntityState.entity_type == "chapter")
                .one()
            )
            assert state.payload_json == server_payload
'''
if text.count(old) != 1:
    raise RuntimeError(f"conflict expectation anchor changed: {text.count(old)}")
text = text.replace(old, new, 1)
old = '''            assert resolved_state.revision > server_revision
            assert service.list_conflicts(status="open") == []
'''
new = '''            assert resolved_state.revision > server_revision
            assert resolved_state.payload_json["_record_type"] == "chapter"
            assert resolved_state.payload_json["content"] == "手机离线版本"
            assert resolved_state.payload_json["current_version"] == 2
            assert service.list_conflicts(status="open") == []
'''
if text.count(old) != 1:
    raise RuntimeError(f"resolved state anchor changed: {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
