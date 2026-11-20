use std::{collections::{HashMap,HashSet},path::PathBuf,sync::Arc};
use anyhow::{anyhow,Context,Result};
use chrono::Utc;
use reqwest::Client;
use serde_json::{json,Value};
use tokio::sync::{Mutex,RwLock};
use crate::{engine::Engine,model::{PluginSettings,RunLog,SavedData,Settings}};

pub struct AppState {
 pub core:Arc<Engine>,
 pub http:Client,
 pub data:RwLock<SavedData>,
 path:PathBuf,
 persistence:Mutex<()>,
}

impl AppState {
 pub fn load(core:Arc<Engine>, dir:PathBuf)->Result<Self>{
  std::fs::create_dir_all(&dir)?;
  let path=dir.join("hestia-settings.json");
  let data=if path.exists(){
   serde_json::from_slice(&std::fs::read(&path)?).context("Invalid settings JSON")?
  }else{SavedData::default()};
  Ok(Self {core,http:Client::builder().timeout(std::time::Duration::from_secs(180)).build()?,
   data:RwLock::new(data),path,persistence:Mutex::new(())})
 }
 pub async fn snapshot(&self)->SavedData{self.data.read().await.clone()}
 pub async fn save(&self)->Result<()>{
  let _lock=self.persistence.lock().await;
  let bytes=serde_json::to_vec_pretty(&*self.data.read().await)?;
  let temporary=self.path.with_extension("json.tmp");
  std::fs::write(&temporary,bytes)?;
  #[cfg(unix)]{
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(&temporary,std::fs::Permissions::from_mode(0o600))?;
  }
  std::fs::rename(temporary,&self.path)?;
  Ok(())
 }
 pub async fn reload_index(&self)->Result<usize>{
  let disabled:HashSet<String>=self.data.read().await.plugins.iter()
   .filter_map(|(k,v)|if v.enabled{None}else{Some(k.clone())}).collect();
  self.core.reload(&disabled).await
 }
 pub async fn plugin_values(&self,id:&str)->HashMap<String,String>{
  let data=self.data.read().await;
  let mut result=HashMap::new();
  if let Some(plugin)=data.plugins.get(id){
   for (key,value) in &plugin.values{result.insert(key.clone(),value.clone());}
  }
  result
 }
 pub async fn llm(&self,system:&str,user:&str,json_mode:bool)->Result<String>{
  let settings=self.data.read().await.settings.clone();
  let mut body=json!({
   "model":settings.llm_model,"temperature":0,
   "messages":[{"role":"system","content":system},{"role":"user","content":user}]
  });
  if json_mode{body["response_format"]=json!({"type":"json_object"});}
  let api_key=std::env::var("HESTIA_LLM_API_KEY").ok();
  let mut request=self.http.post(&settings.llm_url).json(&body);
  if let Some(key)=api_key{request=request.bearer_auth(key);}
  let response=request.send().await.context("LLM unavailable")?.error_for_status()
    .context("LLM HTTP error")?;
  let payload:Value=response.json().await?;
  let text=payload["choices"][0]["message"]["content"].as_str()
    .ok_or_else(||anyhow!("Malformed LLM response"))?;
  Ok(text.trim().to_string())
 }
 pub async fn record(&self,source:&str,id:&str,status:&str,text:&str){
  let mut data=self.data.write().await;
  data.history.push(RunLog{at:Utc::now().to_rfc3339(),source:source.into(),
    tool_id:id.into(),status:status.into(),
    text:text.chars().take(600).collect()});
  if data.history.len()>200{
    let excess=data.history.len()-200;data.history.drain(0..excess);
  }
  drop(data);
  if let Err(e)=self.save().await{tracing::warn!("Cannot persist run log: {e}");}
 }
 pub async fn set_settings(&self,settings:Settings)->Result<()>{
  if !(0.0..=1.0).contains(&settings.route_min_score)||
     !(0.0..=1.0).contains(&settings.route_min_margin)||
     settings.llm_model.is_empty()||
     ![&settings.llm_url,&settings.stt_url,&settings.tts_url]
         .iter().all(|url|url.starts_with("http://")||url.starts_with("https://")){
      return Err(anyhow!("Invalid LLM/routing/audio settings"));
  }
  self.data.write().await.settings=settings;self.save().await
 }
 pub async fn set_plugin(&self,id:&str,enabled:Option<bool>,values:HashMap<String,String>)->Result<()>{
  let tool=self.core.get_tool(id).await;
  // Disabled tools are not in the active index; discover their manifest
  // by id from the on-disk catalog during validation.
  let tool=match tool {
    Some(x)=>x,
    None=>{
      let all=self.core.available_manifests()?;
      all.into_iter().find(|x|x.id==id).ok_or_else(||anyhow!("Tool not found"))?
    }
  };
  for key in values.keys(){
    if !tool.config.contains_key(key){return Err(anyhow!("Unknown field {key}"));}
  }
  let mut data=self.data.write().await;
  let entry=data.plugins.entry(id.to_owned()).or_insert_with(PluginSettings::default);
  if let Some(value)=enabled{entry.enabled=value;}
  for (key,value) in values{
   if value=="__CLEAR__"{entry.values.remove(&key);}
   else if !value.is_empty(){entry.values.insert(key,value);}
  }
  drop(data);
  self.save().await?;
  self.reload_index().await?;
  Ok(())
 }
}
