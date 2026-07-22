"""Two ways a build can be stale or misconfigured without anyone being told.

Both are the same failure shape as the July incident, where regenerated index
tables never propagated: something that governs the output changed, every stage
logged success, and the artifact silently disagreed with its inputs.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from swp_data import config
from swp_data.lineage import dependency_version, fingerprint
from swp_data.settings import (Settings, config_candidates, load_settings,
                               resolve_config_file)


class TestStageContracts:
    """Fingerprints must cover the constants that change output on their own.

    Hashing only data inputs left every stage blind to its own configuration:
    halving OMNI_MAX_GAP_MINUTES rewrites the drivers while every input file
    stays byte-identical, and the stage would log "skip (inputs unchanged)".
    """

    def test_grid_size_changes_the_interpolate_contract(self):
        base = fingerprint(config.INTERPOLATE_CONTRACT, ["a"])
        altered = fingerprint((1, config.LMAX, config.NLAT + 1, config.NLON), ["a"])
        assert base != altered

    def test_altitude_grid_changes_the_iri_contract(self):
        base = fingerprint(config.IRI_CONTRACT)
        altered = fingerprint((1, config.IRI_CONTRACT[1][:-1]))
        assert base != altered

    def test_fill_tolerance_changes_the_omni_contract(self):
        base = fingerprint(config.OMNI_CONTRACT)
        altered = fingerprint((1, tuple(config.DRIVER_FEATURES),
                               config.OMNI_MAX_GAP_MINUTES / 2,
                               config.KP_WINDOW_HOURS))
        assert base != altered

    def test_driver_channel_set_changes_the_omni_contract(self):
        base = fingerprint(config.OMNI_CONTRACT)
        altered = fingerprint((1, tuple(config.DRIVER_FEATURES[:-1]),
                               config.OMNI_MAX_GAP_MINUTES, config.KP_WINDOW_HOURS))
        assert base != altered

    @pytest.mark.parametrize("name", ["INTERPOLATE_CONTRACT", "IRI_CONTRACT",
                                      "DTEC_CONTRACT", "OMNI_CONTRACT"])
    def test_every_contract_carries_a_manual_version(self, name):
        """The leading integer is bumped when semantics change but constants do not."""
        contract = getattr(config, name)
        assert isinstance(contract, tuple) and isinstance(contract[0], int)

    def test_pyiri_version_participates(self):
        """PyIRI's output IS the baseline, so an upgrade must force a rebuild."""
        version = dependency_version("PyIRI")
        assert version not in ("absent", "unknown")
        assert fingerprint(config.IRI_CONTRACT, version) != \
               fingerprint(config.IRI_CONTRACT, "0.0.0")

    def test_absent_package_reports_rather_than_raising(self):
        assert dependency_version("definitely-not-installed-xyz") == "absent"


class TestConfigDiscovery:
    def test_explicitly_requested_missing_file_raises(self, tmp_path, monkeypatch):
        """Silently falling back to defaults is what made this invisible."""
        monkeypatch.setenv("SWP_CONFIG_FILE", str(tmp_path / "typo.yaml"))
        with pytest.raises(FileNotFoundError, match="requested explicitly"):
            resolve_config_file()

    def test_explicit_argument_missing_also_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            resolve_config_file(tmp_path / "nope.yaml")

    @pytest.mark.parametrize("value", ["", "   "])
    def test_blank_env_var_means_unset(self, value, tmp_path, monkeypatch):
        """Shells routinely export an empty var; that is not a request for ''."""
        monkeypatch.setenv("SWP_CONFIG_FILE", value)
        (tmp_path / "config.yaml").write_text("center: COD\n")
        monkeypatch.chdir(tmp_path)
        assert resolve_config_file() == tmp_path / "config.yaml"

    def test_cwd_config_is_found(self, tmp_path, monkeypatch):
        """A wheel install cannot see the package-relative file; CWD must work."""
        monkeypatch.delenv("SWP_CONFIG_FILE", raising=False)
        (tmp_path / "config.yaml").write_text("center: JPL\n")
        monkeypatch.chdir(tmp_path)
        assert resolve_config_file() == tmp_path / "config.yaml"
        assert load_settings().center == "JPL"

    def test_cwd_takes_precedence_over_package(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SWP_CONFIG_FILE", raising=False)
        (tmp_path / "config.yaml").write_text("center: ESA\n")
        monkeypatch.chdir(tmp_path)
        assert config_candidates()[0] == tmp_path / "config.yaml"
        assert load_settings().center == "ESA"

    def test_absent_config_is_reported_on_settings(self, tmp_path, monkeypatch):
        """None is the signal the CLI turns into a warning."""
        monkeypatch.delenv("SWP_CONFIG_FILE", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("swp_data.settings._PACKAGE_CONFIG_FILE",
                            tmp_path / "absent" / "config.yaml")
        settings = load_settings()
        assert settings.config_file is None
        assert settings.center == Settings().center      # defaults stood in

    def test_loaded_file_is_recorded(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SWP_CONFIG_FILE", raising=False)
        path = tmp_path / "config.yaml"
        path.write_text("center: COD\n")
        monkeypatch.chdir(tmp_path)
        assert load_settings().config_file == path

    def test_env_still_overrides_the_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SWP_CONFIG_FILE", raising=False)
        (tmp_path / "config.yaml").write_text("center: JPL\ndata_root: from_file\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("SWP_DATA_ROOT", "from_env")
        settings = load_settings()
        assert settings.center == "JPL"
        assert settings.data_root == Path("from_env")

    def test_config_file_key_in_yaml_is_ignored(self, tmp_path, monkeypatch):
        """It is provenance, not a setting -- and would collide on construction."""
        monkeypatch.delenv("SWP_CONFIG_FILE", raising=False)
        path = tmp_path / "config.yaml"
        path.write_text("config_file: /somewhere/else\ncenter: COD\n")
        monkeypatch.chdir(tmp_path)
        assert load_settings().config_file == path
