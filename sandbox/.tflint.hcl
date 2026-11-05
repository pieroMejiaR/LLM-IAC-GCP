# Configuración de TFLint del agente (TFLINT_CONFIG).
# La versión del ruleset google se fija aquí; el Dockerfile la instala con tflint --init.
# Fuera del contenedor: tflint --init --config sandbox/.tflint.hcl
config {
  call_module_type = "none"
}

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

plugin "google" {
  enabled = true
  version = "0.40.0"
  source  = "github.com/terraform-linters/tflint-ruleset-google"
}
