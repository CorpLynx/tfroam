variable "prefix" {}

resource "terraform_data" "l1" {
  input = "${var.prefix}-l1"
}

module "level2" {
  source = "./level2"
  prefix = terraform_data.l1.output
}

output "deep" {
  value = module.level2.deep
}
