use std::{str::FromStr,sync::Arc};
use chrono::{DateTime,Duration,Utc};
use cron::Schedule;
use serde_json::json;
use tokio::time::{interval,Duration as TokioDuration};
use tracing::{warn,info};
use crate::{state::AppState,model::ScheduleJob};

pub fn parse_cron(value:&str)->anyhow::Result<Schedule>{
 let fields:Vec<_>=value.split_whitespace().collect();
 let standard=match fields.len(){
  5=>format!("0 {}",fields.join(" ")),
  6=>fields.join(" "),
  7=>fields.join(" "),
  _=>anyhow::bail!("Use a 5-field cron expression (minute hour day month weekday)")
 };
 Ok(Schedule::from_str(&standard)?)
}
pub fn validate_job(job:&ScheduleJob)->anyhow::Result<()>{
 if job.id.len()<2||job.id.len()>80||
  !job.id.chars().all(|c|c.is_ascii_alphanumeric()||"_-".contains(c)){
  anyhow::bail!("Invalid schedule ID");
 }
 let _=parse_cron(&job.cron)?;
 if !job.args.is_object(){anyhow::bail!("Scheduled tool arguments must be JSON object");}
 Ok(())
}
pub fn spawn_scheduler(state:Arc<AppState>){
 tokio::spawn(async move {
  let mut ticker=interval(TokioDuration::from_secs(5));
  loop{
   ticker.tick().await;
   let now=Utc::now();
   let jobs=state.snapshot().await.schedules;
   for job in jobs.into_iter().filter(|x|x.enabled){
    let schedule=match parse_cron(&job.cron){Ok(x)=>x,Err(_)=>continue};
    // No long catch-up after downtime: only a firing time within the last 5s.
    let from=now-Duration::seconds(6);
    let due=schedule.after(&from).next().is_some_and(|date|date<=now);
    if !due{continue;}
    let last=job.last_run.as_ref().and_then(|x|DateTime::parse_from_rfc3339(x).ok())
      .map(|t|t.with_timezone(&Utc));
    if last.is_some_and(|t|t>=from){continue;}
    let manifest=match state.core.get_tool(&job.tool_id).await{
      Some(x)=>x,None=>continue
    };
    if !manifest.schedule_allowed||manifest.requires_confirmation{continue;}
    // Advance timestamp before execution, so a slow tool is not retriggered.
    {
      let mut data=state.data.write().await;
      if let Some(item)=data.schedules.iter_mut().find(|x|x.id==job.id){
       item.last_run=Some(now.to_rfc3339());
       item.last_status=Some("running".into());
      }
    }
    if let Err(e)=state.save().await{warn!("Schedule save failure: {e}");}
    let state_copy=state.clone();
    tokio::spawn(async move{
     let outcome=state_copy.core.run_tool(&state_copy,&job.tool_id,
       "Exécution récurrente",Some(job.args.clone()),json!({}),false,0).await;
     info!("Schedule {}: {} {}",job.id,outcome.status,outcome.text);
     state_copy.record("scheduled",&job.tool_id,&outcome.status,&outcome.text).await;
     {
      let mut data=state_copy.data.write().await;
      if let Some(item)=data.schedules.iter_mut().find(|x|x.id==job.id){
       item.last_status=Some(outcome.status);
      }
     }
     if let Err(e)=state_copy.save().await{warn!("Schedule save failure: {e}");}
    });
   }
  }
 });
}
#[cfg(test)]
mod tests{
 use super::*;
 #[test]fn accepts_five_field_cron(){assert!(parse_cron("*/15 * * * *").is_ok());}
 #[test]fn rejects_invalid_cron(){assert!(parse_cron("bad").is_err());}
}
