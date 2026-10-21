from __future__ import annotations

from figai.frontends.tray import build_tray_icon, generate_fig_icon


def test_generate_fig_icon_has_expected_size_and_mode() -> None:
    image = generate_fig_icon(size=64)
    assert image.size == (64, 64)
    assert image.mode == "RGBA"


def test_generate_fig_icon_body_pixel_is_plum_colored() -> None:
    image = generate_fig_icon(size=64)
    # Center of the teardrop body should be the plum body color (fully opaque).
    pixel = image.getpixel((32, 38))
    assert pixel[3] == 255  # opaque
    assert pixel[0] > pixel[1]  # more red than green -> purple/plum, not the green leaf


def test_generate_fig_icon_corner_is_transparent() -> None:
    image = generate_fig_icon(size=64)
    assert image.getpixel((0, 0))[3] == 0


def test_build_tray_icon_has_four_menu_items_in_order() -> None:
    icon = build_tray_icon(status_provider=lambda: "status text")
    labels = [str(item) for item in icon.menu.items]
    assert labels == ["Status", "Open Logs", "Restart", "Quit"]


def test_status_menu_item_notifies_with_status_provider_text() -> None:
    notified: list[str] = []

    class FakeIcon:
        def notify(self, message: str) -> None:
            notified.append(message)

    icon = build_tray_icon(status_provider=lambda: "Fig is online.")
    status_item = icon.menu.items[0]

    status_item(FakeIcon())

    assert notified == ["Fig is online."]


def test_open_logs_menu_item_calls_injected_callback() -> None:
    calls = []
    icon = build_tray_icon(status_provider=lambda: "x", on_open_logs=lambda: calls.append(1))
    open_logs_item = icon.menu.items[1]

    open_logs_item(None)

    assert calls == [1]


def test_restart_menu_item_calls_injected_callback() -> None:
    calls = []
    icon = build_tray_icon(status_provider=lambda: "x", on_restart=lambda: calls.append(1))
    restart_item = icon.menu.items[2]

    restart_item(None)

    assert calls == [1]


def test_quit_menu_item_stops_icon_and_calls_injected_callback() -> None:
    calls = []
    stopped = []

    class FakeIcon:
        def stop(self) -> None:
            stopped.append(1)

    icon = build_tray_icon(status_provider=lambda: "x", on_quit=lambda: calls.append(1))
    quit_item = icon.menu.items[3]

    quit_item(FakeIcon())

    assert stopped == [1]
    assert calls == [1]
