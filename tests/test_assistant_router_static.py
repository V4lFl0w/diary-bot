from __future__ import annotations

from pathlib import Path
import re

ASSISTANT = Path(r"app/handlers/assistant.py")
SERVICES = Path(r"app/services/assistant.py")


def _read(p: Path) -> str:
    return p.read_text("utf-8")


# ---------------------------
# 🔴 PROBLEM #1: "Update is not handled" due to missing handler in FSM
# We assert that there EXISTS at least one handler for AssistantFSM.waiting_question with F.text
# ---------------------------
def test_fsm_has_text_handler():
    s = _read(ASSISTANT)
    # must have something like: @router.message(AssistantFSM.waiting_question, ... F.text ...)
    assert re.search(r"@router\.message\(\s*AssistantFSM\.waiting_question[\s\S]*?F\.text", s), (
        "No @router.message(AssistantFSM.waiting_question, ... F.text ...) handler found"
    )


# ---------------------------
# 🔴 PROBLEM: Duplicate decorators on assistant_dialog
# If we see two identical @router.message blocks directly stacked, fail.
# ---------------------------
def test_no_stacked_duplicate_router_message_decorators_for_same_handler():
    s = _read(ASSISTANT)
    # look for two identical consecutive decorators before assistant_dialog
    m = re.search(r"(@router\.message\([\s\S]*?\)\s*\n)\1\s*async def assistant_dialog", s)
    assert not m, "Found duplicated stacked @router.message(...) decorators above assistant_dialog"


# ---------------------------
# 🔴 PROBLEM #3: Callback buttons not working
# We assert that callback handlers exist for media:ok/media:alts/media:hint
# ---------------------------
def test_media_callbacks_exist():
    s = _read(ASSISTANT)
    for cb in ("media:ok", "media:alts", "media:hint"):
        assert re.search(rf"@router\.callback_query\(\s*F\.data\s*==\s*[\"\']{cb}[\"\']\s*\)", s), (
            "Missing callback handler for callback"
        )


# ---------------------------
# 🔴 PROBLEM: "Digits don't work" (choice picking)
# This is implemented in services run_assistant() with _looks_like_choice.
# We assert the logic exists in app/services/assistant.py
# ---------------------------
def test_services_has_choice_logic():
    s = _read(SERVICES)
    assert "_looks_like_choice" in s, "services: _looks_like_choice not referenced"
    assert re.search(r"if\s+st\s+and\s+_looks_like_choice\(", s), (
        "services: no choice handling block (st and _looks_like_choice)"
    )


# ---------------------------
# 🔴 PROBLEM #5: menu disappears because assistant answers without reply_markup
# This is a UX choice, but your complaint says it MUST be returned.
# We enforce: assistant_entry uses reply_markup=get_main_kb
# and assistant_exit uses reply_markup=get_main_kb
# and assistant_dialog answers ALWAYS include reply_markup=get_main_kb at least once in function.
# ---------------------------
def test_assistant_entry_and_exit_have_main_keyboard():
    s = _read(ASSISTANT)
    # entry
    assert re.search(
        r"async def assistant_entry\([\s\S]*?await m\.answer\([\s\S]*?reply_markup\s*=\s*get_main_kb", s
    ), "assistant_entry: no reply_markup=get_main_kb(...) in answer"
    # exit
    assert re.search(r"async def assistant_exit\([\s\S]*?await m\.answer\([\s\S]*?reply_markup\s*=\s*get_main_kb", s), (
        "assistant_exit: no reply_markup=get_main_kb(...) in answer"
    )


def test_assistant_dialog_returns_keyboard_somewhere():
    s = _read(ASSISTANT)
    # locate assistant_dialog body
    m = re.search(r"async def assistant_dialog\([\s\S]*?\n(?=@router\.|\Z)", s)
    assert m, "assistant_dialog not found"
    body = m.group(0)
    assert "reply_markup=get_main_kb" in body, "assistant_dialog: no reply_markup=get_main_kb(...) in replies"


# ---------------------------
# 🔴 PROBLEM: Menu clicks swallowed inside assistant FSM
# You currently have assistant_menu_exit clearing state.
# If it does not raise SkipHandler, menus router might never receive it.
# We enforce: assistant_menu_exit MUST raise SkipHandler after state.clear()
# ---------------------------
def test_menu_click_is_not_swallowed_in_assistant_fsm():
    s = _read(ASSISTANT)
    m = re.search(r"async def assistant_menu_exit\([\s\S]*?\n(?=@router\.|\Z)", s)
    assert m, "assistant_menu_exit not found"
    body = m.group(0)
    assert "await state.clear()" in body, "assistant_menu_exit must clear FSM"
    assert "SkipHandler" in s, "SkipHandler import missing"
    assert re.search(r"raise\s+SkipHandler\(\)", body), (
        "assistant_menu_exit must `raise SkipHandler()` to pass update to menus"
    )


# ---------------------------
# 🔴 PROBLEM #2: "vision.tmdb.hit then continues and overwrites"
# In vision function, after items found, it should return.
# We'll assert that after `vision.tmdb.hit` log, there is a `break` and then final `if items: return`.
# This is a structural sanity check.
# ---------------------------
def test_vision_tmdb_break_and_return_present():
    s = _read(SERVICES)
    # must have loop that breaks when items found
    assert "vision.tmdb.hit" in s, "No vision.tmdb.hit log in services"
    # require a `break` after hit logging within the TMDb try loop
    # (heuristic: ensure at least one `break` exists in the same region)
    idx = s.find("vision.tmdb.hit")
    window = s[max(0, idx - 800) : idx + 1200]
    assert "break" in window, "Vision TMDb loop seems not to break after hit (risk overwriting result)"
    # and final if items: return reply
    assert re.search(r"if\s+items:\s*[\s\S]{0,400}return\s+reply", s), (
        "Vision path: missing `if items: ... return reply` (risk overwriting / falling through)"
    )
