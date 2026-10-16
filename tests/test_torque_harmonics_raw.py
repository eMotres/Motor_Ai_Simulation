"""Independent cosine amplitudes verify all resolved raw harmonic bins."""
import numpy as np
import pytest

from motor_ai_sim.simulation.sb_postproc import torque_harmonics


@pytest.mark.parametrize("count,order,amplitude", [
    (72, 12, 1e-5), (8, 4, 1.25), (9, 4, .7), (100, 43, .2),
])
def test_raw_spectrum_keeps_precision_all_orders_and_nyquist(count, order, amplitude):
    signal = amplitude * np.cos(order * np.arange(count) * 2 * np.pi / count)
    orders, amplitudes = torque_harmonics(signal, count)
    assert orders == list(range(1, count // 2 + 1))
    assert amplitudes[order - 1] == pytest.approx(amplitude, rel=1e-12, abs=1e-15)
    assert max(value for index, value in enumerate(amplitudes) if index != order - 1) < 1e-12


def test_empty_waveform_has_no_spectrum():
    assert torque_harmonics([], 72) == ([], [])


@pytest.mark.parametrize("count,periods,bin_index", [
    (108, 1.5, 8), (72, 1.001, 6), (144, 2., 10), (36, .5, 3),
    (3, 3 / 72, 1), (2, 2 / 72, 1),
])
def test_every_window_and_short_waveform_retains_all_resolved_bins(count, periods, bin_index):
    signal = 2.123456789 + .7 * np.cos(bin_index * np.arange(count) * 2 * np.pi / count)
    orders, amplitudes = torque_harmonics(signal, 72, step_periods=periods / count)
    np.testing.assert_allclose(orders, np.arange(1, count // 2 + 1) / periods)
    assert len(amplitudes) == count // 2
    assert amplitudes[bin_index - 1] == pytest.approx(.7, rel=1e-12, abs=1e-15)


def test_late_samples_are_not_truncated_to_the_first_period():
    # First period is zero. A final impulse contributes to every resolved bin.
    signal = np.zeros(144)
    signal[-1] = 9.
    orders, amplitudes = torque_harmonics(signal, 72)
    assert orders == [k / 2 for k in range(1, 73)]
    np.testing.assert_allclose(amplitudes[:-1], 9. / 72, atol=1e-15)
    assert amplitudes[-1] == pytest.approx(9. / 144)


def test_transform_receives_every_original_sample_including_dc(monkeypatch):
    original = np.fft.rfft
    received = []

    def spy(samples):
        received.append(samples.copy())
        return original(samples)

    monkeypatch.setattr(np.fft, "rfft", spy)
    signal = np.array([2.123456789])
    assert torque_harmonics(signal, 72) == ([], [])  # DC-only one-sample window
    np.testing.assert_array_equal(received[0], signal)


@pytest.mark.parametrize("step", [float("nan"), float("inf"), 0., -.1])
def test_invalid_sampling_step_is_reported_honestly(step):
    with pytest.raises(ValueError, match="sampling step"):
        torque_harmonics([1., 2.], 72, step_periods=step)
