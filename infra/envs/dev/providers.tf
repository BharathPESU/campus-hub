provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = "campus-hub"
      Env       = var.env
      ManagedBy = "terraform"
    }
  }
}

provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}
