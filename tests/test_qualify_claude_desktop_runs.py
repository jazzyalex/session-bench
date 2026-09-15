from scripts.qualify_claude_desktop_runs import _observer_with_exact_submissions


def test_exact_gui_submissions_replace_template_without_using_native_as_author() -> None:
    observer = {
        "events": [
            {"kind": "user_turn", "fields": {"text": "template-r1"}},
            {"kind": "assistant_response", "fields": {}},
            {"kind": "user_turn", "fields": {"text": "template-r2"}},
        ]
    }
    gui = {"submitted_prompts": {"r1": "actual-r1", "r2": "actual-r2"}}
    native = {"turns": [{"text": "actual-r1"}, {"text": "actual-r2"}]}

    result = _observer_with_exact_submissions(observer, gui, native)

    turns = [row["fields"]["text"] for row in result["events"] if row["kind"] == "user_turn"]
    assert turns == ["actual-r1", "actual-r2"]
    assert observer["events"][0]["fields"]["text"] == "template-r1"
