# A made-up three-VPC AWS network used for the README screenshots and GIFs.
# It plans without an AWS account: the provider is told to skip every credential and account
# check, and nothing here reads from AWS (no data sources). Never apply it.
#
#   cd docs/demo && rover            # or: terraform init && terraform plan

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region                      = var.region
  access_key                  = "demo-not-a-key"
  secret_key                  = "demo-not-a-key"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
  skip_region_validation      = true
}

resource "aws_ec2_transit_gateway" "core" {
  description                     = "acme core"
  default_route_table_association = "disable"
  default_route_table_propagation = "disable"
  tags                            = { Name = "acme-core" }
}

resource "aws_ec2_transit_gateway_route_table" "spokes" {
  transit_gateway_id = aws_ec2_transit_gateway.core.id
  tags               = { Name = "spokes" }
}

resource "aws_ec2_transit_gateway_route_table" "egress" {
  transit_gateway_id = aws_ec2_transit_gateway.core.id
  tags               = { Name = "egress" }
}

module "vpc_egress" {
  source             = "./modules/vpc"
  name               = "egress"
  cidr               = "10.0.0.0/16"
  azs                = var.azs
  public             = true
  transit_gateway_id = aws_ec2_transit_gateway.core.id
}

module "vpc_prod" {
  source             = "./modules/vpc"
  name               = "prod"
  cidr               = "10.10.0.0/16"
  azs                = var.azs
  transit_gateway_id = aws_ec2_transit_gateway.core.id
}

module "vpc_dev" {
  source             = "./modules/vpc"
  name               = "dev"
  cidr               = "10.20.0.0/16"
  azs                = var.azs
  transit_gateway_id = aws_ec2_transit_gateway.core.id
}

resource "aws_ec2_transit_gateway_route_table_association" "spoke" {
  for_each = {
    prod = module.vpc_prod.tgw_attachment_id
    dev  = module.vpc_dev.tgw_attachment_id
  }
  transit_gateway_attachment_id  = each.value
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.spokes.id
}

resource "aws_ec2_transit_gateway_route_table_association" "egress" {
  transit_gateway_attachment_id  = module.vpc_egress.tgw_attachment_id
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.egress.id
}

resource "aws_ec2_transit_gateway_route" "default_to_egress" {
  destination_cidr_block         = "0.0.0.0/0"
  transit_gateway_attachment_id  = module.vpc_egress.tgw_attachment_id
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.spokes.id
}

resource "aws_ec2_transit_gateway_route" "back_to_spokes" {
  for_each = {
    prod = { cidr = module.vpc_prod.cidr, attachment = module.vpc_prod.tgw_attachment_id }
    dev  = { cidr = module.vpc_dev.cidr, attachment = module.vpc_dev.tgw_attachment_id }
  }
  destination_cidr_block         = each.value.cidr
  transit_gateway_attachment_id  = each.value.attachment
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.egress.id
}
