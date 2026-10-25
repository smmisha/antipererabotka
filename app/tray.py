"""Иконка в трее: подсказка с временем до перерыва и меню статистики и настроек."""

from __future__ import annotations

from collections.abc import Callable

import pystray
from PIL import Image, ImageDraw


class Tray:
    def __init__(
        self,
        *,
        title: str,
        stats_label: str,
        settings_label: str,
        on_stats: Callable[[], None],
        on_settings: Callable[[], None],
    ) -> None:
        menu = pystray.Menu(
            pystray.MenuItem(stats_label, lambda _icon, _item: on_stats(), default=True),
            pystray.MenuItem(settings_label, lambda _icon, _item: on_settings()),
        )
        self.icon = pystray.Icon("antipererabotka", _icon_image(), title, menu)

    def start(self) -> None:
        self.icon.run_detached()

    def set_title(self, text: str) -> None:
        if self.icon.title != text:
            self.icon.title = text

    def stop(self) -> None:
        self.icon.stop()


def _icon_image() -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, 60, 60), fill=(58, 123, 213, 255))
    draw.polygon([(32, 12), (46, 34), (40, 46), (24, 46), (18, 34)], fill=(255, 255, 255, 255))
    return image
