terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.28"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region  = var.region
  profile = var.profile

  # O console grava a descricao de cada chave de acesso como tag do usuario IAM (a chave da tag e o
  # id da chave de acesso). Essas tags nao sao gerenciadas pelo Terraform e nao devem ser removidas.
  ignore_tags {
    key_prefixes = ["AKIA"]
  }

  default_tags {
    tags = {
      project   = "cnpj-pipeline"
      managedby = "terraform"
    }
  }
}
