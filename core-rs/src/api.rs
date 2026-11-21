use std::{collections::HashMap,sync::Arc};
use axum::{body::Bytes,extract::{Multipart,Path,State},http::{HeaderMap,StatusCode},
  response::{Html,IntoResponse,Response},routing::{get,post},Json,Router};
use serde::{Deserialize,Serialize};
use serde_json::{json,Value};
use crate::{engine::validate_inputs,model::{Manifest,PluginSettings,ScheduleJob,Settings},scheduler,state::AppState,speech};

type ErrorResponse=(StatusCode,Json<Value>);
fn err(code:StatusCode,message:impl ToString)->ErrorResponse{
 (code,Json(json!({"error":message.to_string()})))
}
fn auth(headers:&HeaderMap)->Result<(),ErrorResponse>{
 let key=std::env::var("HESTIA_API_KEY").unwrap_or_default();
 if key.is_empty(){return Ok(());}
 if headers.get("x-hestia-key").and_then(|x|x.to_str().ok())==Some(key.as_str()){
  return Ok(());
 }
 Err(err(StatusCode::UNAUTHORIZED,"Invalid Hestia key"))
}
#[derive(Deserialize)]
struct TextRequest{text:String}
#[derive(Deserialize)]
struct ChatRequest{
 text:String,#[serde(default)]confirmed:bool,#[serde(default)]context:Value
}
#[derive(Deserialize)]
struct PluginPatch{
 enabled:Option<bool>,#[serde(default)] values:HashMap<String,String>
}
#[derive(Deserialize)]
struct RunRequest{#[serde(default)]args:Value}
#[derive(Deserialize)]
struct KeysPatch{llm_api_key:Option<String>,fallback_api_key:Option<String>}

#[derive(Deserialize,Serialize)]
#[serde(deny_unknown_fields)]
struct AudioDevicePatch{
 input_device:Option<i64>,
 output_device:Option<i64>,
}
pub fn routes(state:Arc<AppState>)->Router{
 Router::new()
 .route("/",get(index))
 .route("/health/live",get(live))
 .route("/health/ready",get(ready))
 .route("/v1/tools",get(tools))
 .route("/v1/tools/reload",post(reload))
 .route("/v1/chat",post(chat))
 .route("/v1/route",post(route))
 .route("/v1/audio/speak",post(speak))
 .route("/v1/audio/transcribe",post(transcribe))
 .route("/v1/audio/ask",post(ask))
 .route("/api/admin/settings",get(get_settings).put(put_settings))
 .route("/api/admin/audio/devices",get(audio_devices).put(save_audio_devices))
 .route("/api/admin/llm-keys",get(get_llm_keys).put(put_llm_keys))
 .route("/api/admin/plugins",get(plugins))
 .route("/api/admin/plugins/{id}",post(update_plugin))
 .route("/api/admin/schedules",get(schedules).post(save_schedule))
 .route("/api/admin/schedules/{id}",axum::routing::delete(delete_schedule))
 .route("/api/admin/history",get(history))
 .route("/api/admin/execute/{id}",post(execute))
 .with_state(state)
}
async fn index()->Html<&'static str>{Html(include_str!("../web/index.html"))}
async fn live()->Json<Value>{Json(json!({"status":"ok","core":"rust"}))}
async fn ready(State(state):State<Arc<AppState>>,headers:HeaderMap)->Response{
 if let Err(e)=auth(&headers){return e.into_response();}
 let settings=state.snapshot().await.settings;
 let urls=[("llm",std::env::var("HESTIA_LLM_HEALTH_URL").unwrap_or_else(|_|"http://llm:8080/health".into())),
           ("stt",std::env::var("HESTIA_STT_HEALTH_URL").unwrap_or_else(|_|"http://stt:8001/health".into())),
           ("tts",std::env::var("HESTIA_TTS_HEALTH_URL").unwrap_or_else(|_|"http://tts:8000/health".into()))];
 let mut services=serde_json::Map::new();let mut ok=true;
 let _=settings;
 for (name,url) in urls {
  let healthy=state.http.get(url).timeout(std::time::Duration::from_secs(3))
    .send().await.map(|r|r.status().is_success()).unwrap_or(false);
  if !healthy{ok=false;}
  services.insert(name.into(),json!(healthy));
 }
 let code=if ok{StatusCode::OK}else{StatusCode::SERVICE_UNAVAILABLE};
 (code,Json(json!({"ready":ok,"services":services}))).into_response()
}
async fn tools(State(state):State<Arc<AppState>>,headers:HeaderMap)->Response{
 if let Err(e)=auth(&headers){return e.into_response();}
 let list=state.core.list().await.into_iter().map(|t|json!({
   "id":t.id,"name":t.name,"description":t.description,
   "examples":t.routing.examples.len(),"schedule_allowed":t.schedule_allowed
 })).collect::<Vec<_>>();
 Json(list).into_response()
}
async fn reload(State(state):State<Arc<AppState>>,headers:HeaderMap)->Response{
 if let Err(e)=auth(&headers){return e.into_response();}
 match state.reload_index().await{
  Ok(n)=>Json(json!({"status":"reloaded","tools":n})).into_response(),
  Err(e)=>err(StatusCode::UNPROCESSABLE_ENTITY,e).into_response()
 }
}
async fn chat(State(state):State<Arc<AppState>>,headers:HeaderMap,Json(req):Json<ChatRequest>)->Response{
 if let Err(e)=auth(&headers){return e.into_response();}
 if req.text.trim().is_empty()||req.text.len()>2000{
  return err(StatusCode::UNPROCESSABLE_ENTITY,"Text must be 1-2000 characters").into_response();
 }
 let result=state.core.run_routed(&state,&req.text,req.context,req.confirmed).await;
 if let Some(ref id)=result.tool_id{
   state.record("chat",id,&result.status,&result.text).await;
 }
 Json(result).into_response()
}
async fn route(State(state):State<Arc<AppState>>,headers:HeaderMap,Json(req):Json<TextRequest>)->Response{
 if let Err(e)=auth(&headers){return e.into_response();}
 let s=state.snapshot().await.settings;
 match state.core.route(&req.text,s.route_min_score,s.route_min_margin).await{
  Ok(r)=>Json(json!(r)).into_response(),
  Err(e)=>err(StatusCode::INTERNAL_SERVER_ERROR,e).into_response()
 }
}
async fn get_settings(State(state):State<Arc<AppState>>,headers:HeaderMap)->Response{
 if let Err(e)=auth(&headers){return e.into_response();}
 Json(state.snapshot().await.settings).into_response()
}
async fn put_settings(State(state):State<Arc<AppState>>,headers:HeaderMap,
                      Json(settings):Json<Settings>)->Response{
 if let Err(e)=auth(&headers){return e.into_response();}
 match state.set_settings(settings).await{
  Ok(())=>Json(json!({"ok":true})).into_response(),
  Err(e)=>err(StatusCode::BAD_REQUEST,e).into_response()
 }
}
// The Rust container does not own the sound devices. Only the listener can
// report PortAudio indices and re-open the microphone/speaker. The fixed
// internal URL is read from the server environment, never from browser input.
async fn proxy_audio_devices(state:&AppState, selection:Option<AudioDevicePatch>)->Response{
 let base=std::env::var("HESTIA_LISTENER_CONTROL_URL")
   .unwrap_or_else(|_|"http://listener:8766".into());
 let url=format!("{}/devices",base.trim_end_matches('/'));
 let mut request=if let Some(value)=selection{
   state.http.put(url).json(&value)
 }else{
   state.http.get(url)
 };
 request=request.timeout(std::time::Duration::from_secs(5));
 let key=std::env::var("HESTIA_API_KEY").unwrap_or_default();
 if !key.is_empty(){request=request.header("X-Hestia-Key",key);}
 let response=match request.send().await{
   Ok(response)=>response,
   Err(error)=>return err(StatusCode::SERVICE_UNAVAILABLE,
     format!("Listener inaccessible : {error}. Vérifie que le service listener est démarré.")).into_response()
 };
 let status=response.status();
 let content=match response.json::<Value>().await{
   Ok(value)=>value,
   Err(error)=>return err(StatusCode::BAD_GATEWAY,
     format!("Réponse invalide du listener : {error}")).into_response()
 };
 if status.is_success(){Json(content).into_response()}
 else{
   let message=content.get("error").and_then(Value::as_str)
     .unwrap_or("Configuration audio refusée");
   err(if status.is_client_error(){StatusCode::BAD_REQUEST}
       else{StatusCode::BAD_GATEWAY},message).into_response()
 }
}
async fn audio_devices(State(state):State<Arc<AppState>>,headers:HeaderMap)->Response{
 if let Err(e)=auth(&headers){return e.into_response();}
 proxy_audio_devices(&state,None).await
}
async fn save_audio_devices(State(state):State<Arc<AppState>>,headers:HeaderMap,
 Json(selection):Json<AudioDevicePatch>)->Response{
 if let Err(e)=auth(&headers){return e.into_response();}
 proxy_audio_devices(&state,Some(selection)).await
}
async fn get_llm_keys(State(state):State<Arc<AppState>>,headers:HeaderMap)->Response{
 if let Err(e)=auth(&headers){return e.into_response();}
 let data=state.snapshot().await;
 Json(json!({"llm_api_key":data.llm_api_key.as_ref().is_some_and(|x|!x.is_empty()),
   "fallback_api_key":data.fallback_api_key.as_ref().is_some_and(|x|!x.is_empty())})).into_response()
}
async fn put_llm_keys(State(state):State<Arc<AppState>>,headers:HeaderMap,
 Json(p):Json<KeysPatch>)->Response{
 if let Err(e)=auth(&headers){return e.into_response();}
 match state.set_llm_keys(p.llm_api_key,p.fallback_api_key).await{
   Ok(())=>Json(json!({"ok":true})).into_response(),
   Err(e)=>err(StatusCode::INTERNAL_SERVER_ERROR,e).into_response()
 }
}
async fn plugins(State(state):State<Arc<AppState>>,headers:HeaderMap)->Response{
 if let Err(e)=auth(&headers){return e.into_response();}
 let data=state.snapshot().await;
 let all=match state.core.available_manifests(){
  Ok(m)=>m,Err(e)=>return err(StatusCode::INTERNAL_SERVER_ERROR,e).into_response()
 };
 let response=all.into_iter().map(|t|{
  let plugin=data.plugins.get(&t.id).cloned().unwrap_or_default();
  let config=t.config.iter().map(|(key,field)|{
   let value=plugin.values.get(key).cloned().unwrap_or_else(||field.default.clone());
   json!({"key":key,"label":field.label,"description":field.description,
     "secret":field.secret,"required":field.required,"configured":!value.is_empty(),
     "value":if field.secret{""}else{value.as_str()}})
  }).collect::<Vec<_>>();
  json!({"id":t.id,"name":t.name,"description":t.description,
    "enabled":plugin.enabled,"config":config,
    "examples":t.routing.examples,"schedule_allowed":t.schedule_allowed})
 }).collect::<Vec<_>>();
 Json(response).into_response()
}
async fn update_plugin(State(state):State<Arc<AppState>>,headers:HeaderMap,
  Path(id):Path<String>,Json(patch):Json<PluginPatch>)->Response{
 if let Err(e)=auth(&headers){return e.into_response();}
 match state.set_plugin(&id,patch.enabled,patch.values).await{
  Ok(())=>Json(json!({"ok":true})).into_response(),
  Err(e)=>err(StatusCode::BAD_REQUEST,e).into_response()
 }
}
async fn schedules(State(state):State<Arc<AppState>>,headers:HeaderMap)->Response{
 if let Err(e)=auth(&headers){return e.into_response();}
 Json(state.snapshot().await.schedules).into_response()
}
async fn save_schedule(State(state):State<Arc<AppState>>,headers:HeaderMap,
  Json(job):Json<ScheduleJob>)->Response{
 if let Err(e)=auth(&headers){return e.into_response();}
 if let Err(e)=scheduler::validate_job(&job){
  return err(StatusCode::BAD_REQUEST,e).into_response();
 }
 let t=match state.core.get_tool(&job.tool_id).await{
  Some(x)=>x,None=>return err(StatusCode::BAD_REQUEST,"Tool disabled or missing").into_response()
 };
 if !t.schedule_allowed||t.requires_confirmation{
  return err(StatusCode::FORBIDDEN,"Tool cannot run unattended").into_response();
 }
 if let Err(e)=validate_inputs(&t.inputs,&job.args){
  return err(StatusCode::BAD_REQUEST,e).into_response();
 }
 {
  let mut data=state.data.write().await;
  if let Some(old)=data.schedules.iter_mut().find(|x|x.id==job.id){*old=job;}
  else{data.schedules.push(job);}
 }
 match state.save().await{
  Ok(())=>Json(json!({"ok":true})).into_response(),
  Err(e)=>err(StatusCode::INTERNAL_SERVER_ERROR,e).into_response()
 }
}
async fn delete_schedule(State(state):State<Arc<AppState>>,headers:HeaderMap,
                         Path(id):Path<String>)->Response{
 if let Err(e)=auth(&headers){return e.into_response();}
 state.data.write().await.schedules.retain(|x|x.id!=id);
 match state.save().await{
  Ok(())=>Json(json!({"ok":true})).into_response(),
  Err(e)=>err(StatusCode::INTERNAL_SERVER_ERROR,e).into_response()
 }
}
async fn history(State(state):State<Arc<AppState>>,headers:HeaderMap)->Response{
 if let Err(e)=auth(&headers){return e.into_response();}
 Json(state.snapshot().await.history).into_response()
}
async fn execute(State(state):State<Arc<AppState>>,headers:HeaderMap,
  Path(id):Path<String>,Json(req):Json<RunRequest>)->Response{
 if let Err(e)=auth(&headers){return e.into_response();}
 let result=state.core.run_tool(&state,&id,"Exécution manuelle",
  Some(req.args),json!({}),false,0).await;
 state.record("manual",&id,&result.status,&result.text).await;
 Json(result).into_response()
}

async fn file_from_multipart(mut multipart:Multipart)->Result<Vec<u8>,String>{
 while let Some(field)=multipart.next_field().await.map_err(|e|e.to_string())?{
  if field.name()==Some("file"){
   let bytes=field.bytes().await.map_err(|e|e.to_string())?;
   if bytes.is_empty()||bytes.len()>10_485_760{
    return Err("Audio must be 1 byte to 10 MiB".into());
   }
   return Ok(bytes.to_vec());
  }
 }
 Err("Missing field file".into())
}
async fn transcribe_audio(state:&AppState,pcm:Vec<u8>)->Result<String,String>{
 let settings=state.snapshot().await.settings;
 let part=reqwest::multipart::Part::bytes(pcm)
  .file_name("voice.wav").mime_str("audio/wav").map_err(|e|e.to_string())?;
 let form=reqwest::multipart::Form::new().part("file",part);
 let response=state.http.post(&settings.stt_url).multipart(form).send().await
  .map_err(|e|e.to_string())?.error_for_status().map_err(|e|e.to_string())?;
 let json:Value=response.json().await.map_err(|e|e.to_string())?;
 json["text"].as_str().map(str::trim).map(str::to_owned)
  .ok_or_else(||"Invalid STT response".into())
}
async fn synthesize_audio(state:&AppState,text:&str)->Result<Bytes,String>{
 if text.is_empty()||text.len()>8000{return Err("Invalid text length".into());}
 let settings=state.snapshot().await.settings;
 let spoken=if settings.normalize_numbers{speech::normalize(text)}else{text.into()};
 let response=state.http.post(&settings.tts_url)
  .form(&[("text",spoken)]).send().await.map_err(|e|e.to_string())?
  .error_for_status().map_err(|e|e.to_string())?;
 let mut wav=response.bytes().await.map_err(|e|e.to_string())?;
 if !wav.starts_with(b"RIFF"){return Err("TTS returned non-WAV data".into());}
 if settings.voice_profile=="mii" {
   use tokio::{io::AsyncWriteExt,process::Command};
   use std::process::Stdio;
   let mut process=Command::new("sox").args(["-t","wav","-","-t","wav","-",
     "pitch","250","highpass","180","lowpass","5000"])
     .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn()
     .map_err(|e|e.to_string())?;
   if let Some(mut input)=process.stdin.take(){
     input.write_all(&wav).await.map_err(|e|e.to_string())?;
   }
   let output=tokio::time::timeout(std::time::Duration::from_secs(45),process.wait_with_output())
     .await.map_err(|_|"SoX timeout".to_string())?.map_err(|e|e.to_string())?;
   if !output.status.success(){return Err("SoX voice filter failed".into());}
   wav=Bytes::from(output.stdout);
 }
 Ok(wav)
}
async fn speak(State(state):State<Arc<AppState>>,headers:HeaderMap,
 Json(request):Json<TextRequest>)->Response{
 if let Err(e)=auth(&headers){return e.into_response();}
 match synthesize_audio(&state,&request.text).await{
  Ok(wav)=>([("content-type","audio/wav")],wav).into_response(),
  Err(e)=>err(StatusCode::BAD_GATEWAY,e).into_response()
 }
}
async fn transcribe(State(state):State<Arc<AppState>>,headers:HeaderMap,
 multipart:Multipart)->Response{
 if let Err(e)=auth(&headers){return e.into_response();}
 let data=match file_from_multipart(multipart).await{
   Ok(x)=>x,Err(e)=>return err(StatusCode::BAD_REQUEST,e).into_response()
 };
 match transcribe_audio(&state,data).await{
  Ok(text)=>Json(json!({"text":text})).into_response(),
  Err(e)=>err(StatusCode::BAD_GATEWAY,e).into_response()
 }
}
async fn ask(State(state):State<Arc<AppState>>,headers:HeaderMap,
 multipart:Multipart)->Response{
 if let Err(e)=auth(&headers){return e.into_response();}
 let data=match file_from_multipart(multipart).await{
   Ok(x)=>x,Err(e)=>return err(StatusCode::BAD_REQUEST,e).into_response()
 };
 let transcript=match transcribe_audio(&state,data).await{
   Ok(x) if !x.is_empty()=>x,
   Ok(_)=>return err(StatusCode::UNPROCESSABLE_ENTITY,"Empty transcription").into_response(),
   Err(e)=>return err(StatusCode::BAD_GATEWAY,e).into_response()
 };
 let answer=state.core.run_routed(&state,&transcript,json!({}),false).await;
 let audio=match synthesize_audio(&state,&answer.text).await{
  Ok(x)=>x,Err(e)=>return err(StatusCode::BAD_GATEWAY,e).into_response()
 };
 if let Some(ref id)=answer.tool_id{
  state.record("voice",id,&answer.status,&answer.text).await;
 }
 use serde_json::json;
 Json(json!({"transcript":transcript,"status":answer.status,"text":answer.text,
   "tool_id":answer.tool_id,"candidates":answer.candidates,
   "audio_base64":encode_base64(&audio)})).into_response()
}
// Standard base64 without adding a standalone crypto dependency.
fn encode_base64(src:&[u8])->String{
 const B:&[u8]=b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
 let mut out=String::with_capacity(src.len().div_ceil(3)*4);
 for chunk in src.chunks(3){
  let x=((chunk[0] as u32)<<16)|((chunk.get(1).copied().unwrap_or(0) as u32)<<8)
    |chunk.get(2).copied().unwrap_or(0) as u32;
  out.push(B[((x>>18)&63) as usize] as char);
  out.push(B[((x>>12)&63) as usize] as char);
  out.push(if chunk.len()>1{B[((x>>6)&63) as usize] as char}else{'='});
  out.push(if chunk.len()>2{B[(x&63) as usize] as char}else{'='});
 }
 out
}
#[cfg(test)]
mod tests {
 use super::*;
 #[test]fn base64_roundtrip_reference(){
  assert_eq!(encode_base64(b"hello"),"aGVsbG8=");
  assert_eq!(encode_base64(b"abc"),"YWJj");
 }
}
