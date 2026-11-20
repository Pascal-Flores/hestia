use std::{collections::{HashMap,HashSet},path::{Path,PathBuf},process::Stdio,sync::{Arc,Mutex}};
use anyhow::{anyhow,Context,Result};
use fastembed::{EmbeddingModel,TextEmbedding,TextInitOptions};
use serde_json::{json,Value};
use tokio::{io::AsyncWriteExt,process::Command,sync::RwLock,time::{timeout,Duration}};
use crate::{model::{Candidate,Manifest,Outcome,Route},state::AppState};

#[derive(Clone)]
struct Indexed {tool:Manifest, vectors:Vec<Vec<f32>>}
pub struct Engine {root:PathBuf, model:Arc<Mutex<TextEmbedding>>,index:RwLock<HashMap<String,Indexed>>}

fn discover(dir:&Path,out:&mut Vec<PathBuf>)->Result<()>{
 if !dir.is_dir(){return Ok(());}
 for item in std::fs::read_dir(dir)?{
  let p=item?.path();if p.is_symlink(){continue;}
  if p.is_dir(){discover(&p,out)?;}
  else if p.file_name().is_some_and(|x|x=="tool.yaml"){out.push(p);}
 }
 Ok(())
}
fn cosine(a:&[f32],b:&[f32])->f32{
 if a.len()!=b.len(){return -1.0;}
 let dot:f32=a.iter().zip(b).map(|(x,y)|x*y).sum();
 let an:f32=a.iter().map(|x|x*x).sum();let bn:f32=b.iter().map(|x|x*x).sum();
 if an==0.0||bn==0.0{0.0}else{dot/(an.sqrt()*bn.sqrt())}
}
impl Engine {
 pub fn new(root:PathBuf,cache:PathBuf)->Result<Self>{
  std::fs::create_dir_all(&cache)?;
  let model=TextEmbedding::try_new(TextInitOptions::new(EmbeddingModel::MultilingualE5Small)
     .with_cache_dir(cache).with_intra_threads(1))?;
  Ok(Self{root,model:Arc::new(Mutex::new(model)),index:RwLock::new(HashMap::new())})
 }
 async fn embed(&self,input:Vec<String>)->Result<Vec<Vec<f32>>>{
  if input.is_empty(){return Ok(vec![]);}
  let model=self.model.clone();
  tokio::task::spawn_blocking(move||->Result<Vec<Vec<f32>>>{
    Ok(model.lock().map_err(|_|anyhow!("Embedding lock poisoned"))?.embed(input,None)?)
  }).await?
 }
 pub async fn reload(&self,disabled:&HashSet<String>)->Result<usize>{
  let mut paths=vec![];discover(&self.root,&mut paths)?;paths.sort();
  let mut next=HashMap::new();let mut ids=HashSet::new();
  for path in paths{
    let mut tool:Manifest=serde_yaml::from_str(&std::fs::read_to_string(&path)?)
       .with_context(||format!("Invalid YAML {}",path.display()))?;
    if tool.id.len()<2||tool.id.len()>80||
       !tool.id.chars().all(|c|c.is_ascii_alphanumeric()||"._-".contains(c)){
       return Err(anyhow!("Invalid plugin id: {}",tool.id));
    }
    if !ids.insert(tool.id.clone()){return Err(anyhow!("Duplicate tool {}",tool.id));}
    if tool.routing.examples.is_empty()||tool.steps.is_empty(){return Err(anyhow!("Incomplete tool {}",tool.id));}
    for step in &tool.steps{
      if !["ai.extract","ai.respond","exec.call","python.call","core.template","tool.call"].contains(&step.uses.as_str()){
        return Err(anyhow!("Unsupported step {}",step.uses));
      }
    }
    if disabled.contains(&tool.id){continue;}
    tool.directory=path.parent().unwrap().to_path_buf();
    let texts=tool.routing.examples.iter().map(|s|format!("passage: {s}")).collect();
    let vectors=self.embed(texts).await?;
    next.insert(tool.id.clone(),Indexed{tool,vectors});
  }
  let count=next.len();*self.index.write().await=next;Ok(count)
 }
 pub async fn list(&self)->Vec<Manifest>{
  let mut list:Vec<_>=self.index.read().await.values().map(|x|x.tool.clone()).collect();
  list.sort_by(|a,b|a.id.cmp(&b.id));list
 }
 pub async fn get_tool(&self,id:&str)->Option<Manifest>{
  self.index.read().await.get(id).map(|x|x.tool.clone())
 }
 pub async fn route(&self,message:&str,min:f32,margin:f32)->Result<Route>{
  if message.trim().is_empty(){return Ok(Route{status:"no_match".into(),tool_id:None,candidates:vec![]});}
  let embeddings=self.embed(vec![format!("query: {message}")]).await?;
  let query=embeddings.first().ok_or_else(||anyhow!("Embedding missing"))?;
  let index=self.index.read().await;
  let mut scores:Vec<Candidate>=index.values().map(|x|Candidate{
   tool_id:x.tool.id.clone(),
   score:x.vectors.iter().map(|v|cosine(query,v)).fold(-1.0f32,f32::max)
  }).collect();
  scores.sort_by(|a,b|b.score.total_cmp(&a.score));
  let best=scores.first();let threshold=best.and_then(|b|index.get(&b.tool_id))
    .and_then(|x|x.tool.routing.threshold).unwrap_or(min);
  let status=match best{
   None=>"no_match",
   Some(x) if x.score<threshold=>"no_match",
   Some(x) if scores.get(1).is_some_and(|other|x.score-other.score<margin)=>"ambiguous",
   _=>"matched"
  };
  Ok(Route{status:status.into(),
   tool_id:if status=="matched"{best.map(|x|x.tool_id.clone())}else{None},
   candidates:scores.into_iter().take(3).collect()})
 }
 pub async fn run_routed(&self,state:&AppState,message:&str,context:Value,confirmed:bool)->Outcome{
  let settings=state.snapshot().await.settings;
  let route=match self.route(message,settings.route_min_score,settings.route_min_margin).await{
    Ok(x)=>x,Err(e)=>return Outcome::message("error",format!("Erreur routage : {e}"))
  };
  let mut outcome=match route.status.as_str(){
   "matched"=>self.run_tool(state,route.tool_id.as_deref().unwrap_or(""),message,None,context,confirmed,0).await,
   "ambiguous"=>Outcome::message("ambiguous",format!("Peux-tu préciser ? J'hésite entre : {}.",
     route.candidates.iter().take(2).map(|x|x.tool_id.as_str()).collect::<Vec<_>>().join(", "))),
   _=>match state.llm("Tu es Hestia. Réponds en français. Aucune action n'a été effectuée.",message,false).await{
      Ok(text)=>Outcome::message("conversation",text),
      Err(_)=>Outcome::message("no_tool","Aucun outil ne correspond à cette demande.")
   }
  };
  outcome.candidates=route.candidates;outcome
 }
 pub async fn run_tool(&self,state:&AppState,id:&str,message:&str,preset:Option<Value>,
   context:Value,confirmed:bool,depth:usize)->Outcome{
  if depth>=5{return Outcome::message("error","Profondeur maximale atteinte");}
  let tool=match self.get_tool(id).await{
   Some(x)=>x,None=>return Outcome::message("no_tool",format!("Outil {id} absent ou désactivé"))
  };
  let mut out=Outcome::message("completed","");out.tool_id=Some(id.into());
  if tool.requires_confirmation&&!confirmed{
   out.status="confirmation_required".into();out.text=format!("Confirmer {} ?",tool.name);return out;
  }
  let config=state.plugin_values(id).await;
  for (key,field) in &tool.config{
   if field.required&&config.get(key).map(|x|x.trim().is_empty()).unwrap_or(true){
    out.status="configuration_required".into();
    out.text=format!("Configurer {} dans le tableau de bord pour utiliser {}.",field.label,tool.name);return out;
   }
  }
  let mut outputs=HashMap::new();let mut last=preset.clone().unwrap_or(json!({}));
  if let Some(ref values)=preset{
   if let Err(e)=validate_inputs(&tool.inputs,values){
    out.status="error".into();out.text=e.to_string();return out;
   }
  }
  for step in &tool.steps{
   let opts=resolve(&step.options,&outputs);
   let result:Result<Value>=match step.uses.as_str(){
    "ai.extract"=>{
     if let Some(ref args)=preset{Ok(args.clone())}else{
      let system=format!("Tu extrais UNIQUEMENT les paramètres de l'outil '{}': {}. \
      Réponds uniquement avec un objet JSON, statut ready/parameters, missing_parameters/question ou not_applicable. \
      N'invente rien. Schéma JSON : {}. Instructions : {}",
      tool.name,tool.description,tool.inputs,opts["prompt"].as_str().unwrap_or(""));
      match state.llm(&system,&json!({"message":message,"context":context}).to_string(),true).await{
       Err(e)=>Err(e),
       Ok(answer)=>match serde_json::from_str::<Value>(&answer){
        Err(e)=>Err(anyhow!("Invalid extraction JSON: {e}")),
        Ok(data)=>match data["status"].as_str(){
         Some("ready")=>{let params=data["parameters"].clone();validate_inputs(&tool.inputs,&params).map(|_|params)},
         Some("missing_parameters")=>{
           out.status="clarification".into();
           out.text=data["question"].as_str().unwrap_or("Quels paramètres dois-je utiliser ?").into();
           return out;
         },
         Some("not_applicable")=>{out.status="not_applicable".into();out.text="L'outil ne correspond pas.".into();return out;},
         _=>Err(anyhow!("Unknown extraction status"))
        }
       }
      }
     }
    },
    "exec.call"=>{
     let args=opts.get("arguments").cloned().unwrap_or(last.clone());
     match validate_inputs(&tool.inputs,&args){
      Ok(())=>run_exec(&tool,&opts,args,&context,&config).await,Err(e)=>Err(e)
     }
    },
    "python.call"=>{
     let args=opts.get("arguments").cloned().unwrap_or(last.clone());
     match validate_inputs(&tool.inputs,&args){
      Ok(())=>run_python(&tool,&opts,args,&context,&config).await,Err(e)=>Err(e)
     }
    },
    "core.template"=>{
     let data=opts.get("data").unwrap_or(&last);
     Ok(json!({"text":template_string(opts["template"].as_str().unwrap_or(""),data)}))
    },
    "ai.respond"=>{
     let data=opts.get("data").cloned().unwrap_or(last.clone());
     let system=format!("Tu es Hestia. Formule une réponse française courte UNIQUEMENT à partir du résultat réel, \
      jamais d'action inventée. {}",opts["prompt"].as_str().unwrap_or(""));
     state.llm(&system,&json!({"request":message,"tool_result":data}).to_string(),false).await
       .map(|t|json!({"text":t}))
    },
    "tool.call"=>{
     let target=opts["tool"].as_str().unwrap_or("");
     if target==id{Err(anyhow!("Cycle outil"))}else{
       let args=opts.get("arguments").cloned().unwrap_or(last.clone());
       let nested=Box::pin(self.run_tool(state,target,message,Some(args),context.clone(),confirmed,depth+1)).await;
       if nested.status=="completed"{Ok(nested.output.unwrap_or(json!({})))}
       else{Err(anyhow!("{}: {}",target,nested.text))}
     }
    },
    _=>Err(anyhow!("Unsupported step {}",step.uses))
   };
   match result{
    Ok(value)=>{last=value.clone();outputs.insert(step.id.clone(),value);},
    Err(e)=>{out.status="error".into();out.text=format!("Step {}: {e}",step.id);return out;}
   }
  }
  out.text=last.get("text").and_then(Value::as_str).unwrap_or("Outil terminé.").into();
  out.output=Some(last);out
 }
}

pub fn validate_inputs(schema:&Value,args:&Value)->Result<()>{
 let obj=args.as_object().ok_or_else(||anyhow!("Les paramètres doivent être un objet JSON"))?;
 if let Some(required)=schema["required"].as_array(){
  for key in required.iter().filter_map(Value::as_str){
   if !obj.contains_key(key)||obj[key].is_null(){return Err(anyhow!("Paramètre obligatoire : {key}"));}
  }
 }
 let properties=schema.get("properties").and_then(Value::as_object);
 for (name,value) in obj{
  let rule=properties.and_then(|p|p.get(name));
  if rule.is_none()&&schema["additionalProperties"]==false{return Err(anyhow!("Paramètre interdit : {name}"));}
  if let Some(rule)=rule{
   let ok=match rule["type"].as_str(){
    Some("string")=>value.is_string(),Some("number")=>value.is_number(),
    Some("integer")=>value.as_i64().is_some()||value.as_u64().is_some(),
    Some("boolean")=>value.is_boolean(),Some("array")=>value.is_array(),
    Some("object")=>value.is_object(),_=>true};
   if !ok{return Err(anyhow!("Type incorrect : {name}"));}
   if let (Some(n),Some(min))=(value.as_f64(),rule["minimum"].as_f64()){
    if n<min{return Err(anyhow!("{name} inférieur au minimum"));}
   }
   if let (Some(n),Some(max))=(value.as_f64(),rule["maximum"].as_f64()){
    if n>max{return Err(anyhow!("{name} supérieur au maximum"));}
   }
   if let Some(choices)=rule["enum"].as_array(){if !choices.contains(value){return Err(anyhow!("Choix interdit : {name}"));}}
  }
 }
 Ok(())
}
pub fn resolve(value:&Value,outputs:&HashMap<String,Value>)->Value{
 if let Some(s)=value.as_str(){
  if s.starts_with("$")&&s.contains("steps.")&&s.ends_with("}}"){
   let raw=s.trim_start_matches("$").trim_start_matches('{').trim_start_matches('{')
     .trim_end_matches('}').trim().strip_prefix("steps.").unwrap_or("");
   if let Some((step,path))=raw.split_once(".output"){
    let mut data=outputs.get(step).cloned().unwrap_or(Value::Null);
    for p in path.split('.').filter(|x|!x.is_empty()){data=data.get(p).cloned().unwrap_or(Value::Null);}
    return data;
   }
  }
 }
 if let Some(map)=value.as_object(){return Value::Object(map.iter().map(|(k,v)|(k.clone(),resolve(v,outputs))).collect());}
 if let Some(arr)=value.as_array(){return Value::Array(arr.iter().map(|x|resolve(x,outputs)).collect());}
 value.clone()
}
fn template_string(template:&str,data:&Value)->String{
 let re=regex::Regex::new(r"\{\{\s*([\w-]+)\s*\}\}").unwrap();
 re.replace_all(template,|cap:&regex::Captures|{
  data.get(&cap[1]).map(|v|v.as_str().map(str::to_owned).unwrap_or_else(||v.to_string()))
    .unwrap_or_else(||"?".into())
 }).into_owned()
}
async fn process(tool:&Manifest,exec:&str,argv:&[String],args:Value,ctx:&Value,
                 config:&HashMap<String,String>,limit:u64)->Result<Value>{
 if limit==0||limit>120{return Err(anyhow!("Timeout must be 1-120 seconds"));}
 let cmd=if ["python3","bash","sh"].contains(&exec){exec.to_string()}
 else if exec.starts_with("./"){
  let path=tool.directory.join(exec).canonicalize()?;
  if !path.starts_with(tool.directory.canonicalize()?){return Err(anyhow!("Executable outside tool"));}
  path.to_string_lossy().into_owned()
 }else{return Err(anyhow!("Command not allowed: {exec}"));};
 let mut command=Command::new(cmd);
 command.args(argv).current_dir(&tool.directory).env_clear()
  .env("PATH","/usr/local/bin:/usr/bin:/bin").env("PYTHONIOENCODING","utf-8")
  .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped())
  .kill_on_drop(true);
 for (key,value) in config{
  if !key.is_empty()&&key.len()<80&&key.chars().all(|c|c.is_ascii_uppercase()||c.is_ascii_digit()||c=='_'){
    command.env(key,value);
  }
 }
 let mut child=command.spawn().context("Failed starting tool")?;
 if let Some(mut stdin)=child.stdin.take(){
  stdin.write_all(json!({"parameters":args,"context":ctx}).to_string().as_bytes()).await?;
 }
 let result=timeout(Duration::from_secs(limit),child.wait_with_output()).await
   .map_err(|_|anyhow!("Tool timed out"))??;
 if !result.status.success(){
  return Err(anyhow!("Plugin failed: {}",String::from_utf8_lossy(&result.stderr).chars().take(400).collect::<String>()));
 }
 if result.stdout.len()>500_000{return Err(anyhow!("Output too large"));}
 Ok(serde_json::from_slice(&result.stdout).context("Tool stdout must contain JSON")?)
}
async fn run_exec(tool:&Manifest,opts:&Value,args:Value,ctx:&Value,conf:&HashMap<String,String>)->Result<Value>{
 let cmd=opts["command"].as_str().ok_or_else(||anyhow!("Missing command"))?;
 let argv=opts["argv"].as_array().ok_or_else(||anyhow!("Missing argv"))?
   .iter().map(|x|x.as_str().map(str::to_owned).ok_or_else(||anyhow!("Non-string argv")))
   .collect::<Result<Vec<_>>>()?;
 if ["python3","bash","sh"].contains(&cmd){
  let script=argv.first().ok_or_else(||anyhow!("Missing script path"))?;
  if script.starts_with('-')||script.starts_with('/')||script.contains(".."){
   return Err(anyhow!("Script path must be relative"));
  }
  let path=tool.directory.join(script).canonicalize()?;
  if !path.starts_with(tool.directory.canonicalize()?){return Err(anyhow!("Script outside plugin"));}
 }
 process(tool,cmd,&argv,args,ctx,conf,opts["timeout"].as_u64().unwrap_or(20)).await
}
const PYRUN:&str=r#"import importlib.util,json,sys
script,func=sys.argv[1],sys.argv[2]
spec=importlib.util.spec_from_file_location("hestia_plugin",script)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
payload=json.load(sys.stdin)
print(json.dumps(getattr(module,func)(payload["parameters"],payload.get("context",{})),ensure_ascii=False))
"#;
async fn run_python(tool:&Manifest,opts:&Value,args:Value,ctx:&Value,conf:&HashMap<String,String>)->Result<Value>{
 let entry=opts["entrypoint"].as_str().ok_or_else(||anyhow!("Missing entrypoint"))?;
 let (file,func)=entry.split_once(':').ok_or_else(||anyhow!("Invalid entrypoint"))?;
 if file.contains('/')||file.contains("..")||!file.ends_with(".py")
   ||!func.chars().all(|c|c.is_ascii_alphanumeric()||c=='_'){
  return Err(anyhow!("Unsafe entrypoint"));
 }
 let path=tool.directory.join(file).canonicalize()?;
 if !path.starts_with(tool.directory.canonicalize()?){return Err(anyhow!("Script outside tool"));}
 let argv=vec!["-I".into(),"-c".into(),PYRUN.into(),path.to_string_lossy().into_owned(),func.into()];
 process(tool,"python3",&argv,args,ctx,conf,opts["timeout"].as_u64().unwrap_or(20)).await
}
#[cfg(test)]
mod tests{
 use super::*;
 #[test]fn validates(){let s=json!({"type":"object","required":["a"],"additionalProperties":false,
 "properties":{"a":{"type":"number"}}});assert!(validate_inputs(&s,&json!({"a":2})).is_ok());
 assert!(validate_inputs(&s,&json!({"a":"2"})).is_err());}
 #[test]fn resolves(){let mut out=HashMap::new();out.insert("extract".into(),json!({"a":1}));
 assert_eq!(resolve(&json!("${{ steps.extract.output.a }}"),&out),json!(1));}
}
