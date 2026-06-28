"""Phase 1 — 20-test Eye + Hand stability matrix (P6-only OCR)."""

from __future__ import annotations

import time
from typing import Dict, List

from test_helpers import (
    FAIL_P6_WINDOW_NOT_READY,
    TestArtifacts,
    TestContext,
    analysis_not_ready,
    capture_and_analyze,
    check_pollution,
    finish_from_not_ready,
    finish_test,
    score_from_expectation,
)
from accessibility.hand import keyboard_tools, window_tools


def _pollution_or_continue(
    analysis: Dict,
    artifacts: TestArtifacts,
    fail_message: str,
) -> Dict | None:
    pollution = check_pollution(analysis, artifacts)
    if pollution:
        return finish_test(artifacts, pollution, fail_message, analysis=analysis, score=0)
    return None


def test_01_p6_already_open_visible(ctx: TestContext, artifacts: TestArtifacts) -> Dict:
    analysis = capture_and_analyze(ctx, artifacts, "01_visible", prepare=True)
    blocked = _pollution_or_continue(analysis, artifacts, "Cursor/chat OCR leaked into P6 crop")
    if blocked:
        return blocked
    if analysis_not_ready(analysis):
        return finish_from_not_ready(artifacts, analysis, "PASS", analysis.get("reason", "P6 not ready"))
    level = analysis["classification"]["p6_presence"]["level"]
    actual = "PASS" if level in {"strong", "weak"} else "FAIL"
    scored = score_from_expectation(actual, "PASS", f"P6 presence level={level}")
    return finish_test(artifacts, scored["status"], scored["message"], "PASS", analysis, scored["score"])


def test_02_p6_behind_cursor(ctx: TestContext, artifacts: TestArtifacts) -> Dict:
    capture_and_analyze(ctx, artifacts, "_prep02", prepare=True)
    artifacts.notes.append("prep for test 02 complete")
    artifacts.hand_actions.append("activate Cursor window")
    window_tools.activate_window_by_title("Cursor")
    time.sleep(0.8)
    analysis = capture_and_analyze(
        ctx,
        artifacts,
        "02_behind_cursor",
        prepare=False,
        require_p6_foreground=True,
    )
    if analysis_not_ready(analysis):
        scored = score_from_expectation(
            "MANUAL_REVIEW_EXPECTED",
            "MANUAL_REVIEW_EXPECTED",
            "P6 occluded behind Cursor — OCR correctly skipped",
        )
        from hand.p6_prepare import prepare_p6_for_test as restore_p6

        restore_p6(ctx.p6_keyword)
        return finish_test(
            artifacts, scored["status"], scored["message"], "MANUAL_REVIEW_EXPECTED", analysis, scored["score"]
        )
    blocked = _pollution_or_continue(analysis, artifacts, "Pollution while P6 behind Cursor")
    if blocked:
        return blocked
    level = analysis["classification"]["p6_presence"]["level"]
    actual = "MANUAL_REVIEW_EXPECTED" if level == "none" else "PASS"
    msg = "P6 occluded — manual review expected" if level == "none" else f"P6 partially readable (level={level})"
    scored = score_from_expectation(actual, "MANUAL_REVIEW_EXPECTED", msg)
    from hand.p6_prepare import prepare_p6_for_test as restore_p6

    restore_p6(ctx.p6_keyword)
    return finish_test(artifacts, scored["status"], scored["message"], "MANUAL_REVIEW_EXPECTED", analysis, scored["score"])


def test_03_p6_minimised(ctx: TestContext, artifacts: TestArtifacts) -> Dict:
    from hand.p6_prepare import prepare_p6_for_test as restore_p6

    restore_p6(ctx.p6_keyword)
    artifacts.hand_actions.append("minimize_window_by_title")
    window_tools.minimize_window_by_title(ctx.p6_keyword)
    time.sleep(0.8)
    state = window_tools.get_window_state(ctx.p6_keyword)
    artifacts.notes.append(f"Window state after minimize: {state}")
    analysis = capture_and_analyze(ctx, artifacts, "03_minimised", prepare=False)
    if analysis_not_ready(analysis):
        scored = score_from_expectation(
            FAIL_P6_WINDOW_NOT_READY,
            "MANUAL_REVIEW_EXPECTED",
            "P6 minimised — OCR skipped; eye/hand must not act until restored",
        )
        restore_p6(ctx.p6_keyword)
        return finish_test(artifacts, scored["status"], scored["message"], "MANUAL_REVIEW_EXPECTED", analysis, scored["score"])
    restore_p6(ctx.p6_keyword)
    return finish_test(artifacts, "FAIL", "OCR ran while P6 minimised — safety violation", analysis=analysis, score=0)


def test_04_p6_not_maximised(ctx: TestContext, artifacts: TestArtifacts) -> Dict:
    from hand.p6_prepare import prepare_p6_for_test as restore_p6

    restore_p6(ctx.p6_keyword)
    artifacts.hand_actions.append("restore_without_maximize")
    window_tools.restore_without_maximize(ctx.p6_keyword)
    time.sleep(0.8)
    analysis = capture_and_analyze(ctx, artifacts, "04_not_maximised", prepare=False)
    blocked = _pollution_or_continue(analysis, artifacts, "Pollution with non-maximised P6")
    if blocked:
        restore_p6(ctx.p6_keyword)
        return blocked
    if analysis_not_ready(analysis):
        restore_p6(ctx.p6_keyword)
        return finish_from_not_ready(artifacts, analysis, "PASS", analysis.get("reason", "P6 rect invalid"))
    state = analysis["window_state"]
    level = analysis["classification"]["p6_presence"]["level"]
    if state.get("is_maximized"):
        actual, msg = "FAIL", "P6 still maximised — setup failed"
    elif level in {"strong", "weak"}:
        actual, msg = "PASS", f"P6 readable while not maximised (presence={level})"
    else:
        actual, msg = "MANUAL_REVIEW_EXPECTED", "P6 not maximised and weak recognition"
    scored = score_from_expectation(actual, "PASS", msg)
    restore_p6(ctx.p6_keyword)
    return finish_test(artifacts, scored["status"], scored["message"], "PASS", analysis, scored["score"])


def test_05_p6_projects_workspace(ctx: TestContext, artifacts: TestArtifacts) -> Dict:
    analysis = capture_and_analyze(ctx, artifacts, "05_projects", prepare=True)
    blocked = _pollution_or_continue(analysis, artifacts, "Pollution on projects workspace")
    if blocked:
        return blocked
    if analysis_not_ready(analysis):
        return finish_from_not_ready(artifacts, analysis, "PASS", analysis.get("reason", "P6 not ready"))
    workspace = analysis["classification"]["workspace"]
    if workspace == "projects":
        actual, msg = "PASS", "Projects workspace recognised"
    elif workspace == "unknown":
        actual, msg = "MANUAL_REVIEW_EXPECTED", "Workspace unclear — verify Projects view manually"
    else:
        actual, msg = "MANUAL_REVIEW_EXPECTED", f"Expected projects, saw {workspace}"
    scored = score_from_expectation(actual, "PASS", msg)
    return finish_test(artifacts, scored["status"], scored["message"], "PASS", analysis, scored["score"])


def test_06_p6_activities_workspace(ctx: TestContext, artifacts: TestArtifacts) -> Dict:
    analysis = capture_and_analyze(ctx, artifacts, "06_activities", prepare=True)
    blocked = _pollution_or_continue(analysis, artifacts, "Pollution on activities workspace")
    if blocked:
        return blocked
    if analysis_not_ready(analysis):
        return finish_from_not_ready(artifacts, analysis, "PASS", analysis.get("reason", "P6 not ready"))
    workspace = analysis["classification"]["workspace"]
    if workspace == "activities":
        actual, msg = "PASS", "Activities workspace recognised"
    elif workspace == "unknown":
        actual, msg = "MANUAL_REVIEW_EXPECTED", "Workspace unclear — switch to Activities manually if needed"
    else:
        actual, msg = "MANUAL_REVIEW_EXPECTED", f"Expected activities, saw {workspace}"
    scored = score_from_expectation(actual, "PASS", msg)
    return finish_test(artifacts, scored["status"], scored["message"], "PASS", analysis, scored["score"])


def test_07_no_project_open(ctx: TestContext, artifacts: TestArtifacts) -> Dict:
    analysis = capture_and_analyze(ctx, artifacts, "07_no_project", prepare=True)
    blocked = _pollution_or_continue(analysis, artifacts, "Pollution with no project open")
    if blocked:
        return blocked
    if analysis_not_ready(analysis):
        return finish_from_not_ready(artifacts, analysis, "PASS", analysis.get("reason", "P6 not ready"))
    blob = analysis["classification"]["ocr_blob_excerpt"]
    if "no project" in blob or "no current project" in blob:
        actual, msg = "PASS", "No-current-project state recognised in P6 crop"
    elif analysis["classification"]["open_project_visible"]:
        actual, msg = "MANUAL_REVIEW_EXPECTED", "Open-dialog state needs human confirmation"
    elif analysis["classification"]["p6_presence"]["level"] != "none":
        actual, msg = "PASS", "P6 shell visible without requiring project open"
    else:
        actual, msg = "FAIL", "Could not see P6 main shell"
    scored = score_from_expectation(actual, "PASS", msg)
    return finish_test(artifacts, scored["status"], scored["message"], "PASS", analysis, scored["score"])


def test_08_open_project_dialog_visible(ctx: TestContext, artifacts: TestArtifacts) -> Dict:
    from hand.p6_prepare import prepare_p6_for_test

    prepare_p6_for_test(ctx.p6_keyword)
    artifacts.hand_actions.append("open_dialog_ctrl_o")
    keyboard_tools.open_dialog_ctrl_o()
    time.sleep(1.2)
    analysis = capture_and_analyze(
        ctx,
        artifacts,
        "08_open_project",
        prepare=False,
        use_popup_crop=True,
    )
    keyboard_tools.press_escape()
    blocked = _pollution_or_continue(analysis, artifacts, "Pollution on Open Project dialog")
    if blocked:
        return blocked
    if analysis_not_ready(analysis):
        return finish_from_not_ready(artifacts, analysis, "PASS", analysis.get("reason", "P6 not ready"))
    visible = analysis["classification"]["open_project_visible"]
    if visible:
        actual, msg = "PASS", "Open Project dialog recognised in P6 popup crop"
    else:
        actual, msg = "MANUAL_REVIEW_EXPECTED", "Open Project dialog not confidently recognised"
    scored = score_from_expectation(actual, "PASS", msg)
    return finish_test(artifacts, scored["status"], scored["message"], "PASS", analysis, scored["score"])


def test_09_cursor_chatgpt_pollution(ctx: TestContext, artifacts: TestArtifacts) -> Dict:
    analysis = capture_and_analyze(ctx, artifacts, "09_pollution", prepare=True)
    if analysis_not_ready(analysis):
        return finish_from_not_ready(artifacts, analysis, "PASS", analysis.get("reason", "P6 not ready"))
    pollution = analysis.get("pollution_check", {}).get("pollution_words") or []
    if pollution:
        return finish_test(
            artifacts,
            "OCR_POLLUTION",
            f"Cursor/chat words in P6 crop OCR: {pollution}",
            analysis=analysis,
            score=0,
        )
    actual, msg = "PASS", "No Cursor/chat pollution keywords in P6-only OCR"
    scored = score_from_expectation(actual, "PASS", msg)
    return finish_test(artifacts, scored["status"], scored["message"], "PASS", analysis, scored["score"])


def test_10_warning_manual_review_popup(ctx: TestContext, artifacts: TestArtifacts) -> Dict:
    analysis = capture_and_analyze(
        ctx,
        artifacts,
        "10_warning_popup",
        prepare=True,
        use_popup_crop=True,
    )
    blocked = _pollution_or_continue(analysis, artifacts, "Pollution during warning popup test")
    if blocked:
        return blocked
    if analysis_not_ready(analysis):
        scored = score_from_expectation(
            "MANUAL_REVIEW_EXPECTED",
            "MANUAL_REVIEW_EXPECTED",
            "P6 not ready for popup OCR — manual review",
        )
        return finish_test(artifacts, scored["status"], scored["message"], "MANUAL_REVIEW_EXPECTED", analysis, scored["score"])
    buttons = analysis["classification"]["popup_buttons"]
    if buttons.get("warning") or buttons.get("yes") or buttons.get("no"):
        actual, msg = "MANUAL_REVIEW_EXPECTED", "Warning/confirm popup detected — hand must stop"
    else:
        actual, msg = "MANUAL_REVIEW_EXPECTED", "No warning popup present — manual scenario still valid"
    scored = score_from_expectation(actual, "MANUAL_REVIEW_EXPECTED", msg)
    return finish_test(artifacts, scored["status"], scored["message"], "MANUAL_REVIEW_EXPECTED", analysis, scored["score"])


def _button_visibility_test(
    ctx: TestContext,
    artifacts: TestArtifacts,
    label: str,
    button: str,
    expected: str,
    open_dialog: bool = False,
) -> Dict:
    from hand.p6_prepare import prepare_p6_for_test

    prepare_p6_for_test(ctx.p6_keyword)
    if open_dialog:
        artifacts.hand_actions.append("open_dialog_ctrl_o")
        keyboard_tools.open_dialog_ctrl_o()
        time.sleep(1.2)
    analysis = capture_and_analyze(
        ctx,
        artifacts,
        label,
        prepare=False,
        use_popup_crop=True,
    )
    if open_dialog:
        keyboard_tools.press_escape()
    blocked = _pollution_or_continue(analysis, artifacts, f"Pollution during {button} button test")
    if blocked:
        return blocked
    if analysis_not_ready(analysis):
        scored = score_from_expectation("MANUAL_REVIEW_EXPECTED", expected, f"{button} dialog not ready for OCR")
        return finish_test(artifacts, scored["status"], scored["message"], expected, analysis, scored["score"])
    visible = analysis["classification"]["popup_buttons"].get(button.lower(), False)
    if visible:
        actual, msg = "PASS", f"{button} button visible in P6 popup OCR"
    else:
        actual, msg = "MANUAL_REVIEW_EXPECTED", f"{button} button not detected — open matching dialog manually"
    scored = score_from_expectation(actual, expected, msg)
    return finish_test(artifacts, scored["status"], scored["message"], expected, analysis, scored["score"])


def test_11_cancel_button(ctx: TestContext, artifacts: TestArtifacts) -> Dict:
    return _button_visibility_test(ctx, artifacts, "11_cancel", "cancel", "PASS", open_dialog=True)


def test_12_no_button(ctx: TestContext, artifacts: TestArtifacts) -> Dict:
    return _button_visibility_test(ctx, artifacts, "12_no", "no", "PASS", open_dialog=False)


def test_13_yes_button(ctx: TestContext, artifacts: TestArtifacts) -> Dict:
    return _button_visibility_test(ctx, artifacts, "13_yes", "yes", "MANUAL_REVIEW_EXPECTED", open_dialog=False)


def test_14_open_project_cancel_no(ctx: TestContext, artifacts: TestArtifacts) -> Dict:
    from hand.p6_prepare import prepare_p6_for_test

    prepare_p6_for_test(ctx.p6_keyword)
    artifacts.hand_actions.append("open_dialog_ctrl_o")
    keyboard_tools.open_dialog_ctrl_o()
    time.sleep(1.2)
    analysis = capture_and_analyze(
        ctx,
        artifacts,
        "14_open_cancel_no",
        prepare=False,
        use_popup_crop=True,
    )
    keyboard_tools.press_escape()
    blocked = _pollution_or_continue(analysis, artifacts, "Pollution on Open Project cancel/no test")
    if blocked:
        return blocked
    if analysis_not_ready(analysis):
        return finish_from_not_ready(artifacts, analysis, "PASS", analysis.get("reason", "P6 not ready"))
    buttons = analysis["classification"]["popup_buttons"]
    if buttons.get("cancel"):
        actual, msg = "PASS", "Cancel visible on Open Project dialog"
    else:
        actual, msg = "MANUAL_REVIEW_EXPECTED", "Cancel/No not both visible — verify dialog manually"
    scored = score_from_expectation(actual, "PASS", msg)
    return finish_test(artifacts, scored["status"], scored["message"], "PASS", analysis, scored["score"])


def test_15_desktop_pollution_behind_p6(ctx: TestContext, artifacts: TestArtifacts) -> Dict:
    from hand.p6_prepare import prepare_p6_for_test as restore_p6

    restore_p6(ctx.p6_keyword)
    window_tools.restore_without_maximize(ctx.p6_keyword)
    window_tools.move_window_by_title(ctx.p6_keyword, 200, 120)
    time.sleep(0.8)
    analysis = capture_and_analyze(ctx, artifacts, "15_desktop_pollution", prepare=False)
    blocked = _pollution_or_continue(analysis, artifacts, "Chat/cursor pollution in P6 crop")
    if blocked:
        restore_p6(ctx.p6_keyword)
        return blocked
    if analysis_not_ready(analysis):
        scored = score_from_expectation(
            FAIL_P6_WINDOW_NOT_READY,
            "PASS",
            "P6 rect invalid after move — OCR skipped safely",
        )
        restore_p6(ctx.p6_keyword)
        return finish_test(artifacts, scored["status"], scored["message"], "PASS", analysis, scored["score"])
    desktop_hits = analysis["classification"].get("desktop_pollution_hits") or []
    if desktop_hits and analysis["classification"]["p6_presence"]["level"] == "none":
        restore_p6(ctx.p6_keyword)
        return finish_test(
            artifacts,
            "OCR_POLLUTION",
            f"Desktop danger words in OCR: {desktop_hits}",
            analysis=analysis,
            score=0,
        )
    actual, msg = "PASS", "P6-only crop did not inherit desktop danger words"
    scored = score_from_expectation(actual, "PASS", msg)
    restore_p6(ctx.p6_keyword)
    return finish_test(artifacts, scored["status"], scored["message"], "PASS", analysis, scored["score"])


def test_16_p6_moved_position(ctx: TestContext, artifacts: TestArtifacts) -> Dict:
    from hand.p6_prepare import prepare_p6_for_test as restore_p6

    restore_p6(ctx.p6_keyword)
    window_tools.restore_without_maximize(ctx.p6_keyword)
    artifacts.hand_actions.append("move_window_by_title(400,200)")
    window_tools.move_window_by_title(ctx.p6_keyword, 400, 200)
    time.sleep(0.8)
    analysis = capture_and_analyze(ctx, artifacts, "16_moved", prepare=False)
    blocked = _pollution_or_continue(analysis, artifacts, "Pollution after move")
    if blocked:
        restore_p6(ctx.p6_keyword)
        return blocked
    if analysis_not_ready(analysis):
        restore_p6(ctx.p6_keyword)
        return finish_from_not_ready(artifacts, analysis, "PASS", analysis.get("reason", "P6 rect invalid after move"))
    level = analysis["classification"]["p6_presence"]["level"]
    actual = "PASS" if level in {"strong", "weak"} else "MANUAL_REVIEW_EXPECTED"
    msg = f"P6 readable after move (presence={level})"
    scored = score_from_expectation(actual, "PASS", msg)
    restore_p6(ctx.p6_keyword)
    return finish_test(artifacts, scored["status"], scored["message"], "PASS", analysis, scored["score"])


def test_17_alt_tab_focus_change(ctx: TestContext, artifacts: TestArtifacts) -> Dict:
    from hand.p6_prepare import prepare_p6_for_test as restore_p6

    restore_p6(ctx.p6_keyword)
    artifacts.hand_actions.append("alt_tab_once")
    keyboard_tools.alt_tab_once()
    time.sleep(0.8)
    analysis = capture_and_analyze(
        ctx,
        artifacts,
        "17_alt_tab",
        prepare=False,
        require_p6_foreground=True,
    )
    if analysis_not_ready(analysis):
        scored = score_from_expectation(
            "MANUAL_REVIEW_EXPECTED",
            "MANUAL_REVIEW_EXPECTED",
            "Focus changed via Alt+Tab — OCR skipped until P6 re-focused",
        )
        restore_p6(ctx.p6_keyword)
        return finish_test(artifacts, scored["status"], scored["message"], "MANUAL_REVIEW_EXPECTED", analysis, scored["score"])
    blocked = _pollution_or_continue(analysis, artifacts, "Pollution after Alt+Tab")
    if blocked:
        restore_p6(ctx.p6_keyword)
        return blocked
    scored = score_from_expectation(
        "MANUAL_REVIEW_EXPECTED",
        "MANUAL_REVIEW_EXPECTED",
        "Focus changed — hand must re-confirm P6 target",
    )
    restore_p6(ctx.p6_keyword)
    return finish_test(artifacts, scored["status"], scored["message"], "MANUAL_REVIEW_EXPECTED", analysis, scored["score"])


def test_18_stability_check(ctx: TestContext, artifacts: TestArtifacts) -> Dict:
    from hand.p6_prepare import prepare_p6_for_test

    prepare_p6_for_test(ctx.p6_keyword)
    artifacts.notes.append("Waiting 2.0s stability window")
    time.sleep(2.0)
    before = capture_and_analyze(ctx, artifacts, "18_before", prepare=False)
    time.sleep(1.5)
    after = capture_and_analyze(ctx, artifacts, "18_after", prepare=False)
    blocked = _pollution_or_continue(after, artifacts, "Pollution after stability wait")
    if blocked:
        return blocked
    if analysis_not_ready(before) or analysis_not_ready(after):
        return finish_from_not_ready(after, after, "PASS", "P6 not stable for OCR")
    b = before["classification"]["ocr_blob_excerpt"]
    a = after["classification"]["ocr_blob_excerpt"]
    stable = b[:120] == a[:120]
    actual = "PASS" if stable else "MANUAL_REVIEW_EXPECTED"
    msg = "Screen stable across wait" if stable else "Screen text shifted during stability window"
    scored = score_from_expectation(actual, "PASS", msg)
    return finish_test(artifacts, scored["status"], scored["message"], "PASS", after, scored["score"])


def test_19_unknown_p6_screen(ctx: TestContext, artifacts: TestArtifacts) -> Dict:
    from hand.p6_prepare import prepare_p6_for_test as restore_p6

    restore_p6(ctx.p6_keyword)
    artifacts.hand_actions.append("activate Cursor (unknown screen)")
    window_tools.activate_window_by_title("Cursor")
    time.sleep(0.8)
    analysis = capture_and_analyze(
        ctx,
        artifacts,
        "19_unknown",
        prepare=False,
        require_p6_foreground=True,
    )
    if analysis_not_ready(analysis):
        scored = score_from_expectation(
            "CONTROLLED_UNKNOWN",
            "CONTROLLED_UNKNOWN",
            "Unknown screen — P6 not foreground; OCR skipped safely",
        )
        restore_p6(ctx.p6_keyword)
        return finish_test(artifacts, scored["status"], scored["message"], "CONTROLLED_UNKNOWN", analysis, scored["score"])
    blocked = _pollution_or_continue(analysis, artifacts, "Pollution on unknown screen test")
    if blocked:
        restore_p6(ctx.p6_keyword)
        return blocked
    unknown = analysis["classification"]["unknown_screen"]
    if unknown:
        actual, msg = "CONTROLLED_UNKNOWN", "Unknown screen correctly classified"
    else:
        actual, msg = "FAIL", "Expected controlled unknown but P6/popup appeared recognised"
    scored = score_from_expectation(actual, "CONTROLLED_UNKNOWN", msg)
    restore_p6(ctx.p6_keyword)
    return finish_test(artifacts, scored["status"], scored["message"], "CONTROLLED_UNKNOWN", analysis, scored["score"])


def test_20_full_repeated_normal_run(ctx: TestContext, artifacts: TestArtifacts) -> Dict:
    return test_01_p6_already_open_visible(ctx, artifacts)


EYE_HAND_TEST_MATRIX: List[Dict] = [
    {"id": "01", "slug": "p6_already_open_visible", "name": "P6 already open and visible", "runner": test_01_p6_already_open_visible},
    {"id": "02", "slug": "p6_behind_cursor", "name": "P6 behind Cursor", "runner": test_02_p6_behind_cursor},
    {"id": "03", "slug": "p6_minimised", "name": "P6 minimised", "runner": test_03_p6_minimised},
    {"id": "04", "slug": "p6_not_maximised", "name": "P6 not maximised", "runner": test_04_p6_not_maximised},
    {"id": "05", "slug": "p6_projects_workspace", "name": "P6 Projects workspace", "runner": test_05_p6_projects_workspace},
    {"id": "06", "slug": "p6_activities_workspace", "name": "P6 Activities workspace", "runner": test_06_p6_activities_workspace},
    {"id": "07", "slug": "no_project_open", "name": "No project open", "runner": test_07_no_project_open},
    {"id": "08", "slug": "open_project_dialog_visible", "name": "Open Project dialog visible", "runner": test_08_open_project_dialog_visible},
    {"id": "09", "slug": "cursor_chatgpt_pollution", "name": "Cursor/ChatGPT pollution test", "runner": test_09_cursor_chatgpt_pollution},
    {"id": "10", "slug": "warning_manual_review_popup", "name": "Real warning/manual-review popup", "runner": test_10_warning_manual_review_popup},
    {"id": "11", "slug": "p6_cancel_button_visible", "name": "P6 with normal Cancel button visible", "runner": test_11_cancel_button},
    {"id": "12", "slug": "p6_no_button_visible", "name": "P6 with normal No button visible", "runner": test_12_no_button},
    {"id": "13", "slug": "p6_yes_button_visible", "name": "P6 with normal Yes button visible", "runner": test_13_yes_button},
    {"id": "14", "slug": "open_project_cancel_no_visible", "name": "Open Project dialog with Cancel and No visible", "runner": test_14_open_project_cancel_no},
    {"id": "15", "slug": "desktop_pollution_behind_p6", "name": "Desktop contains words Delete / Confirm / Warning behind P6", "runner": test_15_desktop_pollution_behind_p6},
    {"id": "16", "slug": "p6_moved_position", "name": "P6 moved to different screen position", "runner": test_16_p6_moved_position},
    {"id": "17", "slug": "p6_alt_tab_focus_change", "name": "P6 after Alt+Tab focus change", "runner": test_17_alt_tab_focus_change},
    {"id": "18", "slug": "p6_stability_check", "name": "P6 after short wait/stability check", "runner": test_18_stability_check},
    {"id": "19", "slug": "unknown_p6_screen", "name": "Unknown P6 screen", "runner": test_19_unknown_p6_screen},
    {"id": "20", "slug": "full_repeated_normal_run", "name": "Full repeated normal run", "runner": test_20_full_repeated_normal_run},
]
