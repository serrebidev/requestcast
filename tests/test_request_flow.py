import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from requestcast import app


job = {
    "id": "request-flow-test",
    "payload": json.dumps(
        {
            "source": "youtube",
            "kind": "song",
            "title": "Test",
            "_request_after_add": True,
            "_request_ip": "203.0.113.10",
        }
    ),
}
track = {"artist": "Test Artist", "title": "Test Track"}
media = {"unique_id": "test-media-id", "path": "Requests/test.mp3"}

with (
    patch.object(app, "AZURACAST_ENABLED", True),
    patch.object(app, "expand_youtube", return_value=[track]),
    patch.object(app, "download_one", return_value=("downloaded", Path("/tmp/test.mp3"), media)),
    patch.object(app, "submit_azuracast_request", return_value="Request submitted.") as submit,
    patch.object(app, "update_job") as update,
):
    app.process_job(job)

submit.assert_called_once_with("test-media-id", "203.0.113.10")
final_update = update.call_args_list[-1].kwargs
assert final_update["state"] == "completed"
assert "Request submitted" in final_update["detail"]
print("add_and_request_worker_flow=passed")


# With AzuraCast off the same job saves locally and never tries to submit a request.
with (
    patch.object(app, "AZURACAST_ENABLED", False),
    patch.object(app, "expand_youtube", return_value=[dict(track)]),
    patch.object(app, "download_one", return_value=("downloaded", Path("/tmp/test.mp3"), {"path": "/tmp/test.mp3", "unique_id": ""})),
    patch.object(app, "submit_azuracast_request") as never_submitted,
    patch.object(app, "update_job") as local_update,
):
    app.process_job(dict(job))

never_submitted.assert_not_called()
local_detail = local_update.call_args_list[-1].kwargs
assert local_detail["state"] == "completed"
assert "saved to" in local_detail["detail"], local_detail["detail"]
print("local_only_worker_flow=passed")


# A request declined under AzuraCast's own rules is not a failed download: the
# job stays completed, reports the add succeeded, and carries no error field.
with (
    patch.object(app, "AZURACAST_ENABLED", True),
    patch.object(app, "expand_youtube", return_value=[dict(track)]),
    patch.object(app, "download_one", return_value=("downloaded", Path("/tmp/test.mp3"), media)),
    patch.object(
        app,
        "submit_azuracast_request",
        side_effect=RuntimeError(
            "Cannot submit request: This song or artist has been played too recently. "
            "Wait a while before requesting it again."
        ),
    ),
    patch.object(app, "update_job") as refusal_update,
):
    app.process_job(dict(job))

refusal_detail = refusal_update.call_args_list[-1].kwargs
assert refusal_detail["state"] == "completed"
assert "Added successfully" in refusal_detail["detail"], refusal_detail["detail"]
assert "AzuraCast did not accept the request" in refusal_detail["detail"], refusal_detail["detail"]
assert "failed" not in refusal_detail["detail"].lower(), refusal_detail["detail"]
assert not refusal_detail["error"], refusal_detail["error"]
print("policy_refusal_not_a_failure=passed")
