# ─────────────────────────────────────────────────────────────────────────────
# Carbon Nexus — Terraform Infrastructure
# Provisions all Google Cloud resources for the platform
#
# Resources:
#   - Cloud Run (FastAPI backend)
#   - Firestore (database)
#   - Pub/Sub (async messaging)
#   - Cloud Functions (event processor)
#   - Vertex AI (Vector Search index + endpoint)
#   - Secret Manager (API keys)
#   - Firebase Hosting (frontend)
#   - Artifact Registry (Docker images)
# ─────────────────────────────────────────────────────────────────────────────

terraform {
  required_version = ">= 1.7"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.30"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 5.30"
    }
  }
  backend "gcs" {
    bucket = "carbon-nexus-tfstate"
    prefix = "terraform/state"
  }
}

# ─── Variables ────────────────────────────────────────────────────────────────

variable "project_id" {
  description = "GCP Project ID"
  default     = "carbon-nexus-prod"
}

variable "region" {
  description = "Primary GCP region (Mumbai for India latency)"
  default     = "asia-south1"
}

variable "gemini_api_key" {
  description = "Gemini API Key"
  sensitive   = true
}

variable "twilio_account_sid"  { sensitive = true }
variable "twilio_auth_token"   { sensitive = true }
variable "twilio_whatsapp_num" { sensitive = true }

# ─── Provider ─────────────────────────────────────────────────────────────────

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

# ─── Enable APIs ──────────────────────────────────────────────────────────────

locals {
  apis_to_enable = [
    "run.googleapis.com",
    "firestore.googleapis.com",
    "pubsub.googleapis.com",
    "cloudfunctions.googleapis.com",
    "aiplatform.googleapis.com",        # Vertex AI + Vector Search
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "firebase.googleapis.com",
    "identitytoolkit.googleapis.com",   # Firebase Auth
    "cloudbuild.googleapis.com",
    "storage.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis_to_enable)
  service            = each.value
  disable_on_destroy = false
}

# ─── Artifact Registry ────────────────────────────────────────────────────────

resource "google_artifact_registry_repository" "carbon_nexus" {
  location      = var.region
  repository_id = "carbon-nexus"
  format        = "DOCKER"
  description   = "Carbon Nexus Docker images"
  depends_on    = [google_project_service.apis]
}

# ─── Cloud Run — FastAPI Backend ──────────────────────────────────────────────

resource "google_cloud_run_v2_service" "api" {
  name     = "carbon-nexus-api"
  location = var.region

  template {
    scaling {
      min_instance_count = 0   # scale to zero when idle
      max_instance_count = 5
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/carbon-nexus/api:latest"

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
        cpu_idle = true   # only allocate CPU during request processing
      }

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "VERTEX_LOCATION"
        value = var.region
      }
      env {
        name = "VERTEX_INDEX_ENDPOINT_ID"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.vertex_endpoint_id.secret_id
            version = "latest"
          }
        }
      }
    }

    service_account = google_service_account.cloud_run_sa.email
  }

  depends_on = [
    google_project_service.apis,
    google_artifact_registry_repository.carbon_nexus
  ]
}

# Public access for API (auth via Firebase token in app)
resource "google_cloud_run_service_iam_member" "public_access" {
  service  = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ─── Firestore ────────────────────────────────────────────────────────────────

resource "google_firestore_database" "default" {
  name        = "(default)"
  location_id = "asia-south1"
  type        = "FIRESTORE_NATIVE"
  depends_on  = [google_project_service.apis]
}

# Firestore indexes for leaderboard queries
resource "google_firestore_index" "leaderboard_idx" {
  collection = "leaderboard"

  fields {
    field_path = "location"
    order      = "ASCENDING"
  }
  fields {
    field_path = "green_score"
    order      = "DESCENDING"
  }

  depends_on = [google_firestore_database.default]
}

# ─── Pub/Sub ──────────────────────────────────────────────────────────────────

resource "google_pubsub_topic" "carbon_events" {
  name                       = "carbon-events"
  message_retention_duration = "86400s"   # 24h retention
  depends_on                 = [google_project_service.apis]
}

resource "google_pubsub_subscription" "event_processor_sub" {
  name  = "carbon-events-processor-sub"
  topic = google_pubsub_topic.carbon_events.name

  push_config {
    push_endpoint = google_cloudfunctions2_function.event_processor.service_config[0].uri
    oidc_token {
      service_account_email = google_service_account.cloud_run_sa.email
    }
  }

  ack_deadline_seconds       = 30
  message_retention_duration = "86400s"
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "300s"
  }
}

# ─── Cloud Functions v2 — Event Processor ────────────────────────────────────

resource "google_storage_bucket" "functions_source" {
  name     = "${var.project_id}-functions-source"
  location = var.region
}

resource "google_storage_bucket_object" "event_processor_zip" {
  name   = "event_processor.zip"
  bucket = google_storage_bucket.functions_source.name
  source = "../backend/pubsub/event_processor.zip"   # built by CI/CD
}

resource "google_cloudfunctions2_function" "event_processor" {
  name     = "carbon-event-processor"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "process_carbon_event"
    source {
      storage_source {
        bucket = google_storage_bucket.functions_source.name
        object = google_storage_bucket_object.event_processor_zip.name
      }
    }
  }

  service_config {
    min_instance_count    = 0
    max_instance_count    = 10
    available_memory      = "512Mi"
    timeout_seconds       = 60
    service_account_email = google_service_account.cloud_run_sa.email

    environment_variables = {
      GOOGLE_CLOUD_PROJECT = var.project_id
    }
  }

  depends_on = [google_project_service.apis]
}

# ─── Vertex AI Vector Search ──────────────────────────────────────────────────

resource "google_vertex_ai_index" "carbon_kb" {
  provider     = google-beta
  display_name = "carbon-knowledge-base"
  region       = var.region
  description  = "Carbon footprint knowledge base — IPCC, BEE India, Karnataka SAPCC"

  metadata {
    contents_delta_uri = "gs://${var.project_id}-embeddings/"
    config {
      dimensions                  = 768
      approximate_neighbors_count = 10
      distance_measure_type       = "COSINE_DISTANCE"
      algorithm_config {
        tree_ah_config {
          leaf_node_embedding_count    = 500
          leaf_nodes_to_search_percent = 7
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_vertex_ai_index_endpoint" "carbon_kb_endpoint" {
  provider     = google-beta
  display_name = "carbon-kb-endpoint"
  region       = var.region
  depends_on   = [google_vertex_ai_index.carbon_kb]
}

# ─── Secret Manager ───────────────────────────────────────────────────────────

locals {
  secrets = {
    "gemini-api-key"         = var.gemini_api_key
    "twilio-account-sid"     = var.twilio_account_sid
    "twilio-auth-token"      = var.twilio_auth_token
    "twilio-whatsapp-number" = var.twilio_whatsapp_num
  }
}

resource "google_secret_manager_secret" "secrets" {
  for_each  = local.secrets
  secret_id = each.key
  replication { auto {} }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "secret_values" {
  for_each    = local.secrets
  secret      = google_secret_manager_secret.secrets[each.key].id
  secret_data = each.value
}

# Separate secret for Vertex endpoint ID (set after index deploy)
resource "google_secret_manager_secret" "vertex_endpoint_id" {
  secret_id = "vertex-index-endpoint-id"
  replication { auto {} }
}

# ─── Service Account ──────────────────────────────────────────────────────────

resource "google_service_account" "cloud_run_sa" {
  account_id   = "carbon-nexus-sa"
  display_name = "Carbon Nexus Service Account"
}

resource "google_project_iam_member" "sa_roles" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/datastore.user",
    "roles/pubsub.publisher",
    "roles/pubsub.subscriber",
    "roles/secretmanager.secretAccessor",
    "roles/storage.objectViewer",
    "roles/logging.logWriter",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

# ─── GCS Buckets ─────────────────────────────────────────────────────────────

resource "google_storage_bucket" "embeddings" {
  name          = "${var.project_id}-embeddings"
  location      = var.region
  force_destroy = false

  lifecycle_rule {
    action { type = "Delete" }
    condition { age = 365 }
  }
}

# ─── Outputs ──────────────────────────────────────────────────────────────────

output "cloud_run_url" {
  value       = google_cloud_run_v2_service.api.uri
  description = "Cloud Run API base URL — set as VITE_CLOUD_RUN_URL in frontend .env"
}

output "vertex_index_id" {
  value = google_vertex_ai_index.carbon_kb.id
}

output "pubsub_topic" {
  value = google_pubsub_topic.carbon_events.id
}