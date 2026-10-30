from app import winapi


def test_pause_count_is_the_last_line_of_output():
    assert winapi._pause_count("0\r\n") == 0
    assert winapi._pause_count("предупреждение\n2\n") == 2


def test_pause_count_is_none_without_a_number():
    assert winapi._pause_count("") is None
    assert winapi._pause_count("Ошибка\n") is None


def test_app_holds_device_while_its_last_access_has_no_end():
    assert winapi.in_use(None, None) is False
    assert winapi.in_use(100, None) is True
    assert winapi.in_use(100, 0) is True
    assert winapi.in_use(200, 100) is True  # новый доступ начался после конца прошлого
    assert winapi.in_use(100, 200) is False  # прошлый доступ закончился
