"""Exercise response deadlines in real Qt without network calls or real waits."""

import json
import unittest
from unittest import mock

import test_panel as qt_support


class ManualClock:
    def __init__(self):
        self.now = 0
        self.valid = False

    def start(self):
        self.now = 0
        self.valid = True

    def invalidate(self):
        self.valid = False

    def isValid(self):
        return self.valid

    def elapsed(self):
        return self.now


if qt_support._qt_available:
    from PySide2 import QtCore, QtNetwork, QtWidgets
    panel = qt_support.panel

    class Reply(QtCore.QObject):
        readyRead = QtCore.Signal()
        finished = QtCore.Signal()
        metaDataChanged = QtCore.Signal()
        uploadProgress = QtCore.Signal(int, int)

        def __init__(self):
            super().__init__()
            self.buffer = bytearray()
            self.aborted = False
            self.status = 200

        def setReadBufferSize(self, unused_size):
            pass

        def read(self, count):
            result = bytes(self.buffer[:count])
            del self.buffer[:count]
            return QtCore.QByteArray(result)

        def attribute(self, attribute):
            if attribute == QtNetwork.QNetworkRequest.HttpStatusCodeAttribute:
                return self.status

        def error(self):
            return QtNetwork.QNetworkReply.NoError

        def abort(self):
            self.aborted = True
            # Cancellation can synchronously notify the receiver. Its stale
            # reply guard must prevent this from accepting a partial response.
            self.finished.emit()

        def feed(self, data):
            self.buffer.extend(data)
            self.readyRead.emit()

        def complete(self, data):
            self.feed(data)
            self.finished.emit()

    class Manager:
        def __init__(self):
            self.requests = []

        def _request(self, request, body=None):
            reply = Reply()
            self.requests.append((request.url().toString(), body, reply))
            return reply

        def get(self, request):
            return self._request(request)

        def post(self, request, body):
            return self._request(request, json.loads(bytes(body)))


@unittest.skipUnless(qt_support._qt_available, qt_support._qt_reason)
class PanelTimeoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        patches = [
            mock.patch.object(panel.core, "load_preferences", return_value={}),
            mock.patch.object(panel.core, "save_preferences"),
            mock.patch.object(panel.credentials, "load_key", return_value=""),
            mock.patch.object(panel.credentials, "save_key"),
            mock.patch.object(panel.credentials, "delete_key"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.widget = panel.AssistantPanel()
        self.clock = ManualClock()
        self.manager = Manager()
        self.widget._request_clock = self.clock
        self.widget._manager = self.manager
        self.widget._server = "https://studio.invalid"
        self.widget.server_url.setText(self.widget._server)
        self.widget.api_key.setText("test-only-secret")
        self.widget.models.blockSignals(True)
        self.widget.models.addItem("Test model", "test-model")
        self.widget.models.setCurrentIndex(0)
        self.widget.models.blockSignals(False)
        self.widget._connected = True
        self.widget._update_controls()

    def tearDown(self):
        self.widget.disconnect()
        self.widget.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.app.processEvents()

    def send(self, prompt="Read the controls without changing anything.", *, agent=True):
        self.widget.scene_tools.setChecked(agent)
        self.widget.prompt.setPlainText(prompt)
        self.widget._send()
        self.assertIsNotNone(self.widget._reply, self.widget.status.text())
        return self.widget._reply

    def process_queued(self):
        # Only already queued callbacks; no sleeping, network polling, or
        # advancing real time to simulate a multi-minute model response.
        for unused in range(8):
            self.app.processEvents()

    def test_data_extends_inactivity_past_old_three_minute_deadline(self):
        reply = self.send()
        self.clock.now = 180001
        reply.feed(b": upstream is still working\n\n")
        self.widget._timed_out()
        self.assertIs(self.widget._reply, reply)
        self.assertFalse(reply.aborted)
        self.assertEqual(self.widget._timer.interval(), 600000)
        self.clock.now = 780000
        self.widget._timed_out()
        self.assertIs(self.widget._reply, reply)
        self.assertEqual(self.widget._timer.interval(), 1)

    def test_no_data_times_out_with_safe_diagnostics_and_no_retry(self):
        reply = self.send()
        self.clock.now = 600000
        self.widget._timed_out()
        self.process_queued()
        self.assertTrue(reply.aborted)
        self.assertIsNone(self.widget._reply)
        self.assertFalse(self.clock.isValid())
        self.assertFalse(self.widget._timer.isActive())
        self.assertEqual(len(self.manager.requests), 1)
        self.assertIn("No response data arrived for 10 minutes", self.widget.status.text())
        details = self.widget._last_error_details
        self.assertIn("Code: response_idle_timeout", details)
        self.assertIn("request_elapsed_ms: 600000", details)
        self.assertIn("Response bytes: 0", details)
        self.assertIn("response_headers_ms: -1", details)
        self.assertIn("response_first_byte_ms: -1", details)
        self.assertIn("request_uploaded_bytes: -1", details)
        self.assertNotIn("test-only-secret", details)
        self.assertNotIn("Read the controls", details)

    def test_empty_ready_read_does_not_extend_inactivity(self):
        reply = self.send()
        self.clock.now = 599999
        reply.readyRead.emit()
        self.clock.now = 600000
        self.widget._timed_out()
        self.assertIsNone(self.widget._reply)
        self.assertIn("response_idle_timeout", self.widget._last_error_details)

    def test_partial_response_idle_timeout_does_not_expose_content(self):
        reply = self.send()
        self.clock.now = 100000
        reply.feed(b'data: {"choices":[{"delta":{"content":"PRIVATE_SCENE_TEXT"}}]}\n\n')
        self.clock.now = 700000
        self.widget._timed_out()
        self.assertIn("response stopped arriving for 10 minutes", self.widget.status.text())
        self.assertIn("idle_elapsed_ms: 600000", self.widget._last_error_details)
        self.assertNotIn("PRIVATE_SCENE_TEXT", self.widget._last_error_details)
        self.assertNotIn("PRIVATE_SCENE_TEXT", self.widget.transcript.toPlainText())

    def test_heartbeats_cannot_extend_absolute_response_limit(self):
        reply = self.send()
        for elapsed in (500000, 1000000, 1500000, 1799999):
            self.clock.now = elapsed
            reply.feed(b": heartbeat\n\n")
            self.widget._timed_out()
            self.assertIs(self.widget._reply, reply)
        self.clock.now = 1800000
        self.widget._timed_out()
        self.assertTrue(reply.aborted)
        self.assertIn("30-minute total wait limit", self.widget.status.text())
        self.assertIn("Code: response_deadline_exceeded", self.widget._last_error_details)
        self.assertIn("idle_elapsed_ms: 1", self.widget._last_error_details)
        self.assertEqual(len(self.manager.requests), 1)

    def test_partial_tool_request_is_never_executed_after_timeout(self):
        reply = self.send()
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "partial",
                 "type": "function", "function": {"name": "houlm_propose_python",
                 "arguments": '{"code":"PRIVATE_COMMAND_SENTINEL'}}]}, "finish_reason": None}]}
        with mock.patch.object(panel.toolchat, "execute_calls") as execute, mock.patch.object(
                panel.python_actions, "apply") as apply:
            reply.feed(("data: " + json.dumps(event) + "\n\n").encode())
            self.clock.now = 600000
            self.widget._timed_out()
            self.process_queued()
            execute.assert_not_called()
            apply.assert_not_called()
        self.assertIsNone(self.widget._pending_review)
        self.assertEqual(self.widget.transcript.export_commands(), [])
        self.assertIn("No tools from the rejected response were executed", self.widget._last_error_details)
        self.assertNotIn("PRIVATE_COMMAND_SENTINEL", self.widget._last_error_details)
        self.assertEqual(len(self.manager.requests), 1)

    def test_timeout_retains_completed_tool_results_without_replaying(self):
        reply = self.send()
        call = qt_support.PanelTests.tool_call("finished_read")
        with mock.patch.object(panel.toolchat, "execute_calls",
                               side_effect=qt_support.PanelTests.fake_tool_results) as execute:
            reply.complete(qt_support.PanelTests.tool_stream(call))
            self.process_queued()
            self.assertEqual(len(self.manager.requests), 2)
            self.assertEqual(execute.call_count, 1)
            completed = list(self.widget._messages)
            self.assertEqual([message["role"] for message in completed], ["user", "assistant", "tool"])
            self.clock.now = 600000
            self.widget._timed_out()
            self.process_queued()
            self.assertEqual(self.widget._messages, completed)
            self.assertEqual(self.widget._history.active["messages"], completed)
            self.assertEqual(len(self.manager.requests), 2)
            self.assertEqual(execute.call_count, 1)
            self.send("Continue using the completed read; do not repeat it.")
            self.assertEqual(len(self.manager.requests), 3)
            payload = self.manager.requests[-1][1]
            self.assertEqual(sum(message.get("tool_call_id") == "finished_read"
                                 for message in payload["messages"]), 1)
            self.assertEqual(execute.call_count, 1)

    def test_manual_resend_does_not_duplicate_unanswered_prompt(self):
        prompt = "Inspect the scene, without editing."
        self.send(prompt, agent=False)
        self.clock.now = 600000
        self.widget._timed_out()
        self.process_queued()
        self.assertEqual(len(self.manager.requests), 1)
        self.assertEqual(self.widget.prompt.toPlainText(), prompt)
        self.assertEqual(self.widget._messages, [])
        self.widget._send()
        self.assertEqual(len(self.manager.requests), 2)
        self.assertEqual(sum(message.get("content") == prompt
                             for message in self.manager.requests[-1][1]["messages"]), 1)

    def test_cancelled_reply_and_stale_timeout_cannot_abort_new_request(self):
        old_reply = self.send()
        self.clock.now = 599999
        self.widget._cancel_turn()
        self.widget._timed_out()
        self.assertIsNone(self.widget._reply)
        new_reply = self.send("A new read-only request")
        self.clock.now = 300
        old_reply.feed(b"late bytes from cancelled response")
        old_reply.finished.emit()
        self.widget._timed_out()
        self.assertIs(self.widget._reply, new_reply)
        self.assertFalse(new_reply.aborted)
        self.assertEqual(bytes(self.widget._response), b"")
        self.assertEqual(self.widget._timer.interval(), 599700)
        self.assertEqual(self.widget._last_error_details, "")

    def test_success_invalidates_deadline_and_late_timer_is_ignored(self):
        reply = self.send(agent=False)
        reply.complete(b'{"choices":[{"message":{"role":"assistant","content":"Finished"}}]}')
        self.assertIsNone(self.widget._reply)
        self.assertFalse(self.clock.isValid())
        self.assertFalse(self.widget._timer.isActive())
        self.clock.now = 1800000
        self.widget._timed_out()
        self.assertEqual(self.widget._last_error_details, "")
        self.assertIn("Finished", self.widget.transcript.toPlainText())

    def test_model_discovery_keeps_three_minute_absolute_limit(self):
        self.widget.response_wait_minutes.setValue(30)
        self.widget._request("models", "/api/models")
        reply = self.widget._reply
        self.clock.now = 179999
        reply.feed(b" ")
        self.clock.now = 180000
        self.widget._timed_out()
        self.assertTrue(reply.aborted)
        self.assertIn("3-minute total wait limit", self.widget.status.text())
        self.assertIn("response_limit_ms: 180000", self.widget._last_error_details)

    def test_request_phase_tracks_upload_headers_and_body_without_extending_wait(self):
        reply = self.send()
        banner = self.widget.activity_banner
        self.assertIn("Waiting for server", banner.title.text())
        self.assertIn("Request queued", banner.detail.text())
        self.assertGreater(self.widget._request_bytes, 0)
        total = self.widget._request_bytes
        self.clock.now = 35
        reply.uploadProgress.emit(total // 2, total)
        self.assertIn("Sending request", banner.detail.text())
        self.assertEqual(self.widget._request_uploaded_bytes, total // 2)
        self.clock.now = 70
        reply.uploadProgress.emit(total, total)
        self.assertIn("Request uploaded", banner.detail.text())
        self.assertIn("Waiting for HTTP response headers", banner.detail.text())
        self.clock.now = 150
        reply.metaDataChanged.emit()
        self.assertEqual(self.widget._response_headers_ms, 150)
        self.assertIn("Waiting for response data", banner.detail.text())
        self.assertEqual(self.widget._last_response_data_ms, 0)
        self.clock.now = 175
        reply.feed(b": heartbeat\n\n")
        self.assertIn("Receiving response", banner.title.text())
        self.assertEqual(self.widget._response_first_byte_ms, 175)
        self.assertEqual(self.widget._last_response_data_ms, 175)
        self.clock.now = 200
        reply.uploadProgress.emit(total, total)
        reply.metaDataChanged.emit()
        self.assertIn("Receiving response", banner.title.text())
        self.assertEqual(self.widget._response_headers_ms, 150)
        self.assertEqual(self.widget._response_first_byte_ms, 175)

    def test_headers_without_body_timeout_records_distinct_phase(self):
        reply = self.send()
        self.clock.now = 20
        reply.uploadProgress.emit(self.widget._request_bytes, self.widget._request_bytes)
        self.clock.now = 100
        reply.metaDataChanged.emit()
        self.clock.now = 600000
        self.widget._timed_out()
        details = self.widget._last_error_details
        self.assertIn("response_headers_ms: 100", details)
        self.assertIn("response_first_byte_ms: -1", details)
        self.assertIn("request_uploaded_bytes: %d" % self.widget._request_bytes, details)
        self.assertIn("No response data arrived", self.widget.status.text())
        self.assertEqual(len(self.manager.requests), 1)

    def test_request_phase_counters_reset_and_stale_signals_are_ignored(self):
        old_reply = self.send()
        self.clock.now = 50
        old_reply.uploadProgress.emit(self.widget._request_bytes, self.widget._request_bytes)
        old_reply.metaDataChanged.emit()
        old_reply.feed(b": heartbeat\n\n")
        self.widget._cancel_turn()
        new_reply = self.send("Inspect another node.")
        self.assertEqual(self.widget._request_uploaded_bytes, -1)
        self.assertEqual(self.widget._response_headers_ms, -1)
        self.assertEqual(self.widget._response_first_byte_ms, -1)
        self.clock.now = 90
        old_reply.uploadProgress.emit(999999, 999999)
        old_reply.metaDataChanged.emit()
        old_reply.feed(b"late bytes")
        self.assertIs(self.widget._reply, new_reply)
        self.assertEqual(self.widget._request_uploaded_bytes, -1)
        self.assertEqual(self.widget._response_headers_ms, -1)
        self.assertEqual(self.widget._response_first_byte_ms, -1)
        self.assertIn("Request queued", self.widget.activity_banner.detail.text())

    def test_headers_signal_without_http_status_remains_unknown(self):
        reply = self.send()
        for status in (None, 0):
            reply.status = status
            self.clock.now = 25
            reply.metaDataChanged.emit()
            self.assertEqual(self.widget._response_headers_ms, -1)
            self.assertIn("Waiting for HTTP response headers", self.widget.activity_banner.detail.text())
        reply.status = 200
        self.clock.now = 50
        reply.metaDataChanged.emit()
        self.assertEqual(self.widget._response_headers_ms, 50)

    def test_model_discovery_phase_notifications_keep_connecting_label(self):
        self.widget._request("models", "/api/models")
        reply = self.widget._reply
        self.clock.now = 10
        reply.uploadProgress.emit(0, 0)
        reply.metaDataChanged.emit()
        reply.feed(b" ")
        self.assertIn("Connecting", self.widget.activity_banner.title.text())
        self.assertEqual(self.widget._response_headers_ms, 10)
        self.assertEqual(self.widget._response_first_byte_ms, 10)


if __name__ == "__main__":
    unittest.main()
