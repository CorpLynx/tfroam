resource "terraform_data" "unchanged" {
  input = "same"
}

resource "terraform_data" "to_update" {
  input = "new"
}

resource "terraform_data" "to_replace" {
  triggers_replace = "v2"
}

resource "terraform_data" "to_create" {
  input = terraform_data.to_update.output
}
