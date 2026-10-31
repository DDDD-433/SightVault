from scripts import launch


def _lfm3_status(mode='off', budget=0):
    return {
        'instance_root': str(launch.ROOT),
        'vision_backend': 'lfm3',
        'vision_reasoning_mode': mode,
        'vision_thinking_token_budget': budget,
    }


def test_launcher_reuses_lfm3_only_when_thinking_mode_and_budget_match(monkeypatch):
    monkeypatch.setenv('SIGHTVAULT_VISION', 'lfm3')
    monkeypatch.setenv('SIGHTVAULT_LFM3_THINKING_TOKENS', '0')
    assert launch.compatible_instance(_lfm3_status('off', 0))

    monkeypatch.setenv('SIGHTVAULT_LFM3_THINKING_TOKENS', '128')
    assert launch.compatible_instance(_lfm3_status('bounded-thinking', 128))
    assert not launch.compatible_instance(_lfm3_status('off', 0))
    assert launch.provider_mismatch(_lfm3_status('off', 0))
    message = launch.instance_mismatch_message(_lfm3_status('off', 0))
    assert 'thinking mode' in message
    assert "'off'" in message and 'budget 0' in message
    assert "'bounded-thinking'" in message and 'budget 128' in message


def test_launcher_ignores_lfm3_thinking_flag_for_minicpm_and_omni(monkeypatch):
    monkeypatch.setenv('SIGHTVAULT_LFM3_THINKING_TOKENS', '128')
    monkeypatch.setenv('SIGHTVAULT_VISION', 'minicpm')
    minicpm = {'instance_root': str(launch.ROOT), 'vision_backend': 'minicpm'}
    assert launch.compatible_instance(minicpm)
    assert not launch.provider_mismatch(minicpm)

    monkeypatch.setenv('SIGHTVAULT_VISION', 'omni')
    omni = {'instance_root': str(launch.ROOT), 'vision_backend': 'omni'}
    assert launch.compatible_instance(omni)
    assert not launch.provider_mismatch(omni)


def test_launcher_reports_lfm3_restart_mismatch_without_terminating_process(monkeypatch, capsys):
    monkeypatch.setenv('SIGHTVAULT_VISION', 'lfm3')
    monkeypatch.setenv('SIGHTVAULT_LFM3_THINKING_TOKENS', '128')
    monkeypatch.setattr(launch, 'running_status', lambda: _lfm3_status('off', 0))
    monkeypatch.setattr(launch, 'port_busy', lambda: (_ for _ in ()).throw(
        AssertionError('must report mismatch before checking an unrelated port')))
    monkeypatch.setattr(launch.subprocess, 'Popen', lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError('launcher must not kill or start a process on mismatch')))

    assert launch.main() == 1
    output = capsys.readouterr().out
    assert 'thinking mode' in output
    assert 'Stop the existing server and relaunch' in output
    assert 'will not terminate a running process' in output
