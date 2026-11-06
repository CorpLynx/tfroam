variable "envs" {
  type    = set(string)
  default = ["dev", "prod"]
}

locals {
  regions = {
    east = "us-east-1"
    west = "us-west-2"
  }
}

resource "terraform_data" "env" {
  for_each = var.envs
  input    = "env-${each.key}"
}

resource "terraform_data" "region" {
  for_each = local.regions
  input    = each.value
}

resource "terraform_data" "dependent" {
  for_each = terraform_data.env
  input    = each.value.output
}

module "per_region" {
  source   = "./per-region"
  for_each = local.regions
  region   = each.value
}

output "env_ids" {
  value = [for e in terraform_data.env : e.id]
}
