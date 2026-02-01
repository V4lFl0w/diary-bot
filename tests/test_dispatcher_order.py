
def _find_router_index(dp, wanted):
    # aiogram v3: include_router adds to dp.sub_routers list
    subs = list(getattr(dp, "sub_routers", []))
    for i, r in enumerate(subs):
        if r is wanted:
            return i
    return -1

def test_dispatcher_includes_and_order():
    import app.main as main

    dp = main.build_dispatcher()

    # routers should exist
    assert main.assistant_router is not None, "assistant_router is None (import failed)"
    assert main.menus_router is not None, "menus_router is None (import failed)"

    ia = _find_router_index(dp, main.assistant_router)
    im = _find_router_index(dp, main.menus_router)

    assert ia != -1, "assistant_router was not included into Dispatcher"
    assert im != -1, "menus_router was not included into Dispatcher"

    # critical: assistant before menus (menus has catch-all)
    assert ia < im, f"Expected assistant_router before menus_router, got assistant={ia}, menus={im}"
