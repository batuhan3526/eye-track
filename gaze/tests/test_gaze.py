# gaze/tests/test_gaze.py
import math

from eye_gaze.gaze import (iris_ratio, head_proxy_from_matrix, eye_openness,
                           head_translation_proxies)


def test_iris_centered_returns_half():
    assert abs(iris_ratio((5.0, 0.0), (0.0, 0.0), (10.0, 0.0)) - 0.5) < 1e-6


def test_iris_at_left_corner_returns_zero():
    assert abs(iris_ratio((0.0, 0.0), (0.0, 0.0), (10.0, 0.0)) - 0.0) < 1e-6


def test_zero_width_eye_returns_half():
    assert iris_ratio((3.0, 3.0), (3.0, 3.0), (3.0, 3.0)) == 0.5


def test_head_proxy_identity_is_zero():
    identity = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    assert head_proxy_from_matrix(identity) == (0.0, 0.0)


def test_head_proxy_reads_forward_axis():
    m = [[1, 0, 0.3, 0], [0, 1, -0.2, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    assert head_proxy_from_matrix(m) == (0.3, -0.2)


def test_eye_openness_open_greater_than_closed():
    # corners 10 apart (width); open lids 3 apart -> 0.3, closed 0.2 apart -> 0.02
    op = eye_openness((5.0, 1.5), (5.0, -1.5), (0.0, 0.0), (10.0, 0.0))
    cl = eye_openness((5.0, 0.1), (5.0, -0.1), (0.0, 0.0), (10.0, 0.0))
    assert op > cl and abs(op - 0.3) < 1e-6


def test_eye_openness_zero_width_is_zero():
    assert eye_openness((0.0, 1.0), (0.0, -1.0), (3.0, 3.0), (3.0, 3.0)) == 0.0


def test_head_translation_proxies_position_and_scale():
    nose, left, right = (0.5, 0.4), (0.3, 0.4), (0.7, 0.45)
    nx, ny, s = head_translation_proxies(nose, left, right)
    assert (nx, ny) == (0.5, 0.4)
    assert abs(s - math.dist(left, right)) < 1e-9


def test_head_translation_proxies_scale_shrinks_with_distance():
    # user farther from camera -> eyes closer together in the image
    near = head_translation_proxies((0.5, 0.5), (0.3, 0.5), (0.7, 0.5))[2]
    far = head_translation_proxies((0.5, 0.5), (0.4, 0.5), (0.6, 0.5))[2]
    assert far < near
