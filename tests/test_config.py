from __future__ import annotations

from pathlib import Path

import pytest

from rover.config import load_module


def _write(tmp_path: Path, text: str, name: str = "main.tf") -> str:
    (tmp_path / name).write_text(text)
    return str(tmp_path)


def test_basic_blocks_and_positions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write(
        tmp_path,
        """# comment
terraform {
  required_version = ">= 1.0"
  required_providers {
    aws    = "~> 3.0"
    google = { source = "hashicorp/google", version = ">= 3" }
  }
}

variable "region" {
  type        = set(string)
  description = <<-EOT
    where
  EOT
  default     = ["a", "b"]
}

variable "needed" {}
variable "nothing" { default = null }

output "id" {
  value     = 1
  sensitive = true
}

provider "aws" {
  alias = "east"
}

resource "aws_instance" "web" {
  provider = aws.east
}

data "google_thing" "x" {}

module "m" {
  source  = "./m"
  version = "1.2.3"
}
""",
    )
    mod = load_module(".").to_json()
    assert mod["required_core"] == [">= 1.0"]
    assert mod["required_providers"]["aws"] == {"version_constraints": ["~> 3.0"]}
    assert mod["required_providers"]["google"] == {"source": "hashicorp/google", "version_constraints": [">= 3"]}
    assert mod["variables"]["region"] == {
        "name": "region",
        "type": "set(string)",
        "description": "where\n",
        "default": ["a", "b"],
        "required": False,
        "pos": {"filename": "main.tf", "line": 10},
    }
    assert mod["variables"]["needed"]["required"] is True and mod["variables"]["needed"]["default"] is None
    assert mod["variables"]["nothing"]["required"] is False and mod["variables"]["nothing"]["default"] is None
    assert mod["outputs"]["id"] == {"name": "id", "sensitive": True, "pos": {"filename": "main.tf", "line": 21}}
    assert mod["provider_configs"] == {"aws.east": {"name": "aws", "alias": "east"}}
    assert mod["managed_resources"]["aws_instance.web"]["provider"] == {"name": "aws", "alias": "east"}
    assert mod["data_resources"]["data.google_thing.x"]["provider"] == {"name": "google"}
    assert mod["module_calls"]["m"] == {
        "name": "m",
        "source": "./m",
        "version": "1.2.3",
        "pos": {"filename": "main.tf", "line": 36},
    }


def test_non_constant_default_makes_module_unloadable(tmp_path: Path) -> None:
    mod = load_module(_write(tmp_path, 'variable "a" {\n  default = var.other\n}\n'))
    assert mod.has_errors()


def test_syntax_error_and_missing_dir(tmp_path: Path) -> None:
    assert load_module(_write(tmp_path, 'variable "a" {')).has_errors()
    assert load_module(str(tmp_path / "nope")).has_errors()
    assert load_module("").has_errors()


def test_override_files_load_last_and_hidden_files_are_ignored(tmp_path: Path) -> None:
    _write(tmp_path, 'module "m" {\n  source = "./a"\n}\n', "main.tf")
    _write(tmp_path, 'module "m" {\n  version = "2"\n}\n', "main_override.tf")
    _write(tmp_path, 'variable "hidden" {}\n', ".hidden.tf")
    mod = load_module(str(tmp_path))
    assert not mod.has_errors()
    assert mod.module_calls["m"].source == "./a" and mod.module_calls["m"].version == "2"
    assert "hidden" not in mod.variables


def test_empty_directory_has_no_errors(tmp_path: Path) -> None:
    assert not load_module(str(tmp_path)).has_errors()
