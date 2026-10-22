# gaze/tests/test_switch.py
from eye_gaze.switch import EdgeDetector, valid_button


def test_rising_edge_fires_once():
    e = EdgeDetector()
    assert e.update(False) is False
    assert e.update(True) is True    # rising edge
    assert e.update(True) is False   # held -> no repeat
    assert e.update(False) is False


def test_press_release_press_fires_twice():
    e = EdgeDetector()
    assert e.update(True) is True
    assert e.update(False) is False
    assert e.update(True) is True


def test_truthy_values_are_coerced():
    e = EdgeDetector()
    assert e.update(0) is False
    assert e.update(1) is True


def test_valid_button_accepts_known_names_any_case():
    assert valid_button("A") and valid_button("a") and valid_button("dpad_up")


def test_valid_button_rejects_unknown_and_empty():
    assert not valid_button("LT")   # triggers are not buttons
    assert not valid_button("")
    assert not valid_button(None)
