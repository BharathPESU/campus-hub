terraform {
  backend "s3" {
    bucket       = "campus-hub-tfstate-091319139186"
    key          = "envs/dev/terraform.tfstate"
    region       = "ap-south-2"
    encrypt      = true
    use_lockfile = true
  }
}
