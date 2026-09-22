import json

from blisolver.cli import main


def test_plan_has_zero_network_and_does_not_load_production(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    import socket
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    assert main(["songcut", "source.wav", "--plan", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["paid_calls"] == output["network_calls"] == 0
    assert not (tmp_path / "out").exists()


def test_manifest_relative_paths_and_cli_overrides(tmp_path, capsys):
    path = tmp_path / "batch.json"
    path.write_text(json.dumps({"clips": [{"id": "a", "source": "input.wav"}]}))
    assert main(["songcut", "--manifest", str(path), "--normalization", "dynamic", "--plan"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["manifest"]["clips"][0]["source"] == str(tmp_path / "input.wav")
    assert result["manifest"]["audio"]["mode"] == "dynamic"


def test_ambiguous_input_and_validation_do_not_echo_secret(tmp_path, capsys):
    assert main(["songcut", "source.wav", "--manifest", "other.json", "--plan"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"
    path = tmp_path / "batch.json"
    path.write_text(json.dumps({"clips": [{"id": "x", "source": "audio.wav"}],
                                "cloud": {"api_key": "private-token-should-not-echo"}}))
    assert main(["songcut", "--manifest", str(path), "--plan"]) == 1
    assert "private-token-should-not-echo" not in capsys.readouterr().out


def test_cli_audio_stdout_is_one_result(songcut_wav, tmp_path, capsys):
    path = songcut_wav()
    assert main(["songcut", str(path), "--out", str(tmp_path / "run"), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "complete"
