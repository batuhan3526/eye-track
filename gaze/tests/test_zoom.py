# gaze/tests/test_zoom.py
import pytest

from eye_gaze.zoom import grab_region, map_zoom_point


def test_map_center_of_view_is_center_of_region():
    # region 400x300 at (1000, 500); cursor at screen center -> region center
    assert map_zoom_point(1280, 720, 1000, 500, 400, 300, 2561, 1441) == (1200, 650)


def test_map_corners():
    assert map_zoom_point(0, 0, 1000, 500, 400, 300, 2560, 1440) == (1000, 500)
    rx, ry = map_zoom_point(2559, 1439, 1000, 500, 400, 300, 2560, 1440)
    assert (rx, ry) == (1399, 799)  # bottom-right of the region


def test_map_scales_linearly():
    # quarter of the way across the view -> quarter into the region
    rx, _ = map_zoom_point(640, 0, 0, 0, 401, 300, 2561, 1440)
    assert rx == 100


def test_grab_region_returns_image_or_none():
    img = grab_region(0, 0, 64, 48, scale=1.0)
    if img is None:
        pytest.skip("ekran yakalama bu ortamda yok")
    assert img.shape == (48, 64, 3)


# --- scaled displays -------------------------------------------------------
#
# The driver is DPI-unaware on purpose (calibration.json is in logical pixels;
# making it aware would silently rescale every saved calibration). But an
# unaware BitBlt reads PHYSICAL pixels at the coordinates it is given, so on
# a screen at 150% scaling F6 magnified the top-left two thirds of the area
# it later clicked into. Measured: the unaware capture equalled the top-left
# crop of the true screen (mean abs diff 0.07) not the whole of it (19.9).

from eye_gaze.zoom import physical_rect


def test_physical_rect_scales_a_logical_region():
    assert physical_rect(100, 200, 640, 360, 1.5) == (150, 300, 960, 540)


def test_physical_rect_at_100_percent_is_unchanged():
    assert physical_rect(7, 9, 640, 360, 1.0) == (7, 9, 640, 360)


def test_grab_region_captures_the_scaled_area():
    img = grab_region(0, 0, 64, 36, scale=1.5)
    if img is None:
        return                              # no desktop (CI)
    assert img.shape[:2] == (54, 96)


def test_screen_scale_is_a_sane_number():
    from eye_gaze.zoom import screen_scale
    s = screen_scale()
    assert 1.0 <= s <= 4.0
