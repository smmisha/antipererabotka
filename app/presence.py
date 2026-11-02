"""Присутствие у экрана во время перерыва: лицо в кадре обычной веб-камеры. Кадры живут только в памяти и нигде не сохраняются.

present: True — человек у экрана, False — лица нет в кадре, None — неизвестно (камеры нет или кадров давно не было).
None не блокирует отсчёт: при неизвестном присутствии перерыв идёт по времени, как без камеры.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import cv2

CAMERA_INDEXES = (0, 1, 2)
STALE_S = 3.0  # кадров дольше этого не было: присутствие неизвестно
FRAME_PAUSE_S = 0.2  # около пяти кадров в секунду достаточно, камера и процессор меньше нагружаются

_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


def _open_camera() -> cv2.VideoCapture | None:
    for index in CAMERA_INDEXES:
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if cap.isOpened():
            return cap
        cap.release()
    return None


def _has_face(frame) -> bool:
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    return len(_CASCADE.detectMultiScale(grey, 1.2, 5, minSize=(80, 80))) > 0


class Presence:
    def __init__(
        self,
        open_camera: Callable[[], cv2.VideoCapture | None] = _open_camera,
        has_face: Callable[[object], bool] = _has_face,
    ) -> None:
        self._open = open_camera
        self._has_face = has_face
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._face = False
        self._seen_at = 0.0  # когда в последний раз разобрали кадр (time.monotonic)

    @property
    def active(self) -> bool:
        return self._thread is not None

    @property
    def present(self) -> bool | None:
        with self._lock:
            if self._thread is None or time.monotonic() - self._seen_at > STALE_S:
                return None
            return self._face

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="presence", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(3)
        self._thread = None
        with self._lock:
            self._face = False
            self._seen_at = 0.0

    def _run(self) -> None:
        cap = self._open()
        if cap is None:
            return  # камеры нет: present остаётся None, отсчёт идёт по времени
        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                if ok:
                    face = self._has_face(frame)
                    with self._lock:
                        self._face = face
                        self._seen_at = time.monotonic()
                self._stop.wait(FRAME_PAUSE_S)
        finally:
            cap.release()
