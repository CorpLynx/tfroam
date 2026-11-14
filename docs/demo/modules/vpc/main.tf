variable "name" { type = string }
variable "cidr" { type = string }
variable "azs" { type = list(string) }
variable "transit_gateway_id" { type = string }
variable "public" {
  type    = bool
  default = false
}

resource "aws_vpc" "this" {
  cidr_block           = var.cidr
  enable_dns_hostnames = true
  tags                 = { Name = var.name }
}

resource "aws_subnet" "private" {
  for_each          = { for i, az in var.azs : az => i }
  vpc_id            = aws_vpc.this.id
  availability_zone = each.key
  cidr_block        = cidrsubnet(var.cidr, 8, each.value)
  tags              = { Name = "${var.name}-private-${each.key}" }
}

resource "aws_subnet" "public" {
  for_each                = var.public ? { for i, az in var.azs : az => i } : {}
  vpc_id                  = aws_vpc.this.id
  availability_zone       = each.key
  cidr_block              = cidrsubnet(var.cidr, 8, each.value + 100)
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.name}-public-${each.key}" }
}

resource "aws_internet_gateway" "this" {
  count  = var.public ? 1 : 0
  vpc_id = aws_vpc.this.id
}

resource "aws_eip" "nat" {
  count  = var.public ? 1 : 0
  domain = "vpc"
}

resource "aws_nat_gateway" "this" {
  count         = var.public ? 1 : 0
  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public[var.azs[0]].id
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name}-private" }
}

resource "aws_route" "private_default" {
  route_table_id         = aws_route_table.private.id
  destination_cidr_block = "0.0.0.0/0"
  transit_gateway_id     = var.public ? null : var.transit_gateway_id
  nat_gateway_id         = var.public ? aws_nat_gateway.this[0].id : null
  depends_on             = [aws_ec2_transit_gateway_vpc_attachment.this]
}

resource "aws_route_table_association" "private" {
  for_each       = aws_subnet.private
  subnet_id      = each.value.id
  route_table_id = aws_route_table.private.id
}

resource "aws_ec2_transit_gateway_vpc_attachment" "this" {
  transit_gateway_id                              = var.transit_gateway_id
  vpc_id                                          = aws_vpc.this.id
  subnet_ids                                      = [for s in aws_subnet.private : s.id]
  appliance_mode_support                          = var.public ? "enable" : "disable"
  transit_gateway_default_route_table_association = false
  transit_gateway_default_route_table_propagation = false
  tags                                            = { Name = var.name }
}

resource "aws_flow_log" "this" {
  vpc_id               = aws_vpc.this.id
  traffic_type         = "ALL"
  log_destination_type = "s3"
  log_destination      = "arn:aws:s3:::acme-flow-logs/${var.name}"
}

output "vpc_id" { value = aws_vpc.this.id }
output "cidr" { value = aws_vpc.this.cidr_block }
output "private_subnet_ids" { value = [for s in aws_subnet.private : s.id] }
output "tgw_attachment_id" { value = aws_ec2_transit_gateway_vpc_attachment.this.id }
