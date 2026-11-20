use std::{collections::{BTreeMap, HashMap}, path::PathBuf};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConfigField {
    #[serde(default)]
    pub label: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub secret: bool,
    #[serde(default)]
    pub required: bool,
    #[serde(default)]
    pub default: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct Routing {
    #[serde(default)]
    pub examples: Vec<String>,
    pub threshold: Option<f32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Step {
    pub id: String,
    pub uses: String,
    #[serde(default, rename="with")]
    pub options: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Manifest {
    pub id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub routing: Routing,
    #[serde(default = "empty_object_schema")]
    pub inputs: Value,
    #[serde(default)]
    pub steps: Vec<Step>,
    #[serde(default)]
    pub config: BTreeMap<String, ConfigField>,
    #[serde(default)]
    pub permissions: Vec<String>,
    #[serde(default)]
    pub requires_confirmation: bool,
    #[serde(default)]
    pub schedule_allowed: bool,
    #[serde(skip)]
    pub directory: PathBuf,
}
fn empty_object_schema() -> Value {serde_json::json!({"type":"object","properties":{}})}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PluginSettings {
    #[serde(default="enabled")]
    pub enabled: bool,
    #[serde(default)]
    pub values: BTreeMap<String,String>,
}
fn enabled() -> bool {true}
impl Default for PluginSettings {
    fn default() -> Self { Self { enabled: true, values: BTreeMap::new() } }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Settings {
    pub llm_url: String,
    pub llm_model: String,
    #[serde(default)]
    pub fallback_llm_url: String,
    #[serde(default)]
    pub fallback_llm_model: String,
    pub stt_url: String,
    pub tts_url: String,
    pub route_min_score: f32,
    pub route_min_margin: f32,
    pub voice_profile: String,
    pub normalize_numbers: bool,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            llm_url: std::env::var("HESTIA_LLM_URL")
                .unwrap_or_else(|_| "http://llm:8080/v1/chat/completions".into()),
            llm_model: std::env::var("HESTIA_LLM_MODEL")
                .unwrap_or_else(|_| "hestia-local".into()),
            fallback_llm_url: std::env::var("HESTIA_FALLBACK_LLM_URL").unwrap_or_default(),
            fallback_llm_model: std::env::var("HESTIA_FALLBACK_LLM_MODEL")
                .unwrap_or_else(|_| "hestia-local".into()),
            stt_url: std::env::var("HESTIA_STT_URL")
                .unwrap_or_else(|_| "http://stt:8001/v1/transcribe".into()),
            tts_url: std::env::var("HESTIA_TTS_URL")
                .unwrap_or_else(|_| "http://tts:8000/tts".into()),
            route_min_score: std::env::var("HESTIA_ROUTE_MIN_SCORE").ok()
                .and_then(|x| x.parse().ok()).unwrap_or(0.80),
            route_min_margin: std::env::var("HESTIA_ROUTE_MIN_MARGIN").ok()
                .and_then(|x| x.parse().ok()).unwrap_or(0.035),
            voice_profile: std::env::var("HESTIA_VOICE_PROFILE")
                .unwrap_or_else(|_| "natural".into()),
            normalize_numbers: true,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScheduleJob {
    pub id: String,
    pub tool_id: String,
    pub cron: String,
    #[serde(default)]
    pub args: Value,
    #[serde(default="enabled")]
    pub enabled: bool,
    #[serde(default)]
    pub last_run: Option<String>,
    #[serde(default)]
    pub last_status: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunLog {
    pub at: String,
    pub source: String,
    pub tool_id: String,
    pub status: String,
    pub text: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SavedData {
    #[serde(default)]
    pub settings: Settings,
    #[serde(default)]
    pub llm_api_key: Option<String>,
    #[serde(default)]
    pub fallback_api_key: Option<String>,
    #[serde(default)]
    pub plugins: HashMap<String, PluginSettings>,
    #[serde(default)]
    pub schedules: Vec<ScheduleJob>,
    #[serde(default)]
    pub history: Vec<RunLog>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Candidate { pub tool_id: String, pub score: f32 }

#[derive(Debug, Clone, Serialize)]
pub struct Route {
    pub status: String,
    pub tool_id: Option<String>,
    pub candidates: Vec<Candidate>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Outcome {
    pub status: String,
    pub text: String,
    pub tool_id: Option<String>,
    #[serde(skip_serializing_if="Option::is_none")]
    pub output: Option<Value>,
    pub candidates: Vec<Candidate>,
}
impl Outcome {
    pub fn message(status: &str, text: impl Into<String>) -> Self {
        Self { status: status.into(), text: text.into(), tool_id: None,
            output: None, candidates: vec![] }
    }
}
