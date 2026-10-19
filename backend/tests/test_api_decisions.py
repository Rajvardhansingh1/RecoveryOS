"""HTTP surface of the decision flow: suggest, implement-it-yourself, download the plan."""

from test_api import make_client

REPO = {"repo": "demo/acme-api"}


def waiting_run(client):
    run_id = client.post("/v1/runs", json=REPO).json()["id"]
    detail = client.get(f"/v1/runs/{run_id}").json()
    assert detail["status"] == "awaiting_approval"
    return run_id, detail


def test_plan_can_be_downloaded_as_a_markdown_file_once_it_exists():
    client, _ = make_client()
    run_id, _ = waiting_run(client)
    resp = client.get(f"/v1/runs/{run_id}/plan")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert (
        'attachment; filename="release-plan-demo-acme-api-v1.5.0-r1.md"'
        in resp.headers["content-disposition"]
    )
    assert "# Release plan: v1.5.0 for demo/acme-api" in resp.text
    assert "gh release create v1.5.0" in resp.text


def test_no_plan_yet_gives_a_clear_error():
    client, _ = make_client()
    failed = client.post("/v1/runs", json={"repo": "demo/failing-checks"}).json()["id"]
    resp = client.get(f"/v1/runs/{failed}/plan")
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "no_plan_yet"
    assert client.get("/v1/runs/nope/plan").status_code == 404


def test_implementing_it_yourself_ends_the_flow_with_the_plan_delivered():
    client, _ = make_client()
    run_id, _ = waiting_run(client)
    done = client.post(f"/v1/runs/{run_id}/finish", json={"by": "alice"}).json()
    assert done["status"] == "completed" and done["outcome"] == "plan_delivered"
    assert (
        done["steps"][-1]["name"] == "publish_release" and done["steps"][-1]["status"] == "skipped"
    )
    assert not any(e["action"] == "publish" for e in done["remote_effects"])
    assert client.get(f"/v1/runs/{run_id}/plan").status_code == 200  # still downloadable
    again = client.post(f"/v1/runs/{run_id}/finish", json={})
    assert again.status_code == 409 and again.json()["error"]["code"] == "not_awaiting_decision"


def test_publishing_reports_the_published_outcome():
    client, _ = make_client()
    run_id, _ = waiting_run(client)
    done = client.post(f"/v1/runs/{run_id}/approve", json={"by": "alice"}).json()
    assert done["outcome"] == "published"


def test_a_suggestion_produces_a_new_revision_and_waits_again():
    client, _ = make_client()
    run_id, first = waiting_run(client)
    assert first["revision"] == 1 and first["suggestions"] == []

    resp = client.post(
        f"/v1/runs/{run_id}/suggest", json={"text": "Call out the pagination change."}
    )
    assert resp.status_code == 202
    detail = client.get(
        f"/v1/runs/{run_id}"
    ).json()  # background task already ran in the test client
    assert detail["status"] == "awaiting_approval"
    assert detail["revision"] == 2
    assert detail["suggestions"] == ["Call out the pagination change."]
    assert [r["revision"] for r in detail["plan_revisions"]] == [1, 2]
    assert detail["approval"]["status"] == "pending"
    plan_text = client.get(f"/v1/runs/{run_id}/plan").text
    assert "Plan revision 2" in plan_text and "Call out the pagination change." in plan_text
    timeline = [e["event"] for e in client.get(f"/v1/runs/{run_id}/timeline").json()]
    assert "suggestion_received" in timeline and "revision_started" in timeline


def test_after_a_suggestion_the_owner_can_publish_or_finish():
    client, _ = make_client()
    run_id, _ = waiting_run(client)
    client.post(f"/v1/runs/{run_id}/suggest", json={"text": "Reword the notes."})
    done = client.post(f"/v1/runs/{run_id}/approve", json={"by": "alice"}).json()
    assert done["outcome"] == "published"
    assert (
        sum(e["action"] == "publish" and e["kind"] == "applied" for e in done["remote_effects"])
        == 1
    )


def test_suggestions_are_validated_and_limited():
    client, _ = make_client()
    run_id, _ = waiting_run(client)
    for body in ({"text": ""}, {"text": "ab"}, {"text": "x" * 1001}, {}, {"text": "ok ok", "x": 1}):
        resp = client.post(f"/v1/runs/{run_id}/suggest", json=body)
        assert resp.status_code == 422 and resp.json()["error"]["code"] == "invalid_request"
    for i in range(10):
        assert (
            client.post(f"/v1/runs/{run_id}/suggest", json={"text": f"idea {i}"}).status_code == 202
        )
    over = client.post(f"/v1/runs/{run_id}/suggest", json={"text": "one more"})
    assert over.status_code == 409 and over.json()["error"]["code"] == "too_many_suggestions"


def test_suggestions_only_while_waiting_for_a_decision():
    client, _ = make_client()
    run_id, _ = waiting_run(client)
    client.post(f"/v1/runs/{run_id}/finish", json={})
    resp = client.post(f"/v1/runs/{run_id}/suggest", json={"text": "too late now"})
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "not_awaiting_decision"
    assert client.post("/v1/runs/nope/suggest", json={"text": "hello"}).status_code == 404


def test_remote_ledger_entries_carry_a_short_key_for_the_ui():
    client, _ = make_client()
    run_id, detail = waiting_run(client)
    effect = detail["remote_effects"][0]
    assert effect["action"] == "create_draft" and len(effect["key"]) == 8
