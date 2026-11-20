use num2words::{Lang,Num2Words};
use regex::Regex;

fn integer(text:&str)->String{
 let sanitized=text.replace([' ','\u{a0}','\u{202f}'],"");
 if sanitized.len()>16 || (sanitized.len()>1 && sanitized.starts_with('0')){
  const WORDS:[&str;10]=["zéro","un","deux","trois","quatre","cinq","six","sept","huit","neuf"];
  return sanitized.chars().filter_map(|c|c.to_digit(10))
    .map(|n|WORDS[n as usize]).collect::<Vec<_>>().join(" ");
 }
 let n=sanitized.parse::<i64>().unwrap_or(0);
 Num2Words::new(n).lang(Lang::French).to_words().unwrap_or_else(|_|sanitized.clone())
}
fn number(value:&str)->String{
 let s=value.trim().replace(['\u{a0}','\u{202f}'],"");
 let (sign,n)=if let Some(rest)=s.strip_prefix('-'){("moins ",rest)}
  else if let Some(rest)=s.strip_prefix('+'){("plus ",rest)}else{("",s.as_str())};
 if let Some((a,b))=n.split_once(',').or_else(||n.split_once('.')){
   let fraction=if b.starts_with('0')||b.len()>3{
    b.chars().map(|c|integer(&c.to_string())).collect::<Vec<_>>().join(" ")
   }else{integer(b)};
   format!("{sign}{} virgule {fraction}",integer(a))
 }else{format!("{sign}{}",integer(n))}
}
fn part(source:&str)->String{
 let time=Regex::new(r"\b([01]?\d|2[0-3])\s*[hH:]\s*([0-5]\d)\b").unwrap();
 let time_only=Regex::new(r"\b([01]?\d|2[0-3])[hH]\b").unwrap();
 let temp=Regex::new(r"([+\-]?\d+(?:[.,]\d+)?)\s*°\s*([CcFf])").unwrap();
 let percent=Regex::new(r"([+\-]?\d+(?:[.,]\d+)?)\s*%").unwrap();
 let numbers=Regex::new(r"(?P<number>[+\-]?\d+(?:[ \u{a0}\u{202f}]\d{3})*(?:[.,]\d+)?)").unwrap();
 let mut text=time.replace_all(source,|cap:&regex::Captures|{
  let h=cap[1].parse::<u32>().unwrap_or(0);
  let m=cap[2].parse::<u32>().unwrap_or(0);
  format!("{} heure{}{}",if h==1{"une".to_owned()}else{integer(&h.to_string())},
     if h==1{""}else{"s"},if m==0{String::new()}else{format!(" {}",integer(&m.to_string()))})
 }).into_owned();
 text=time_only.replace_all(&text,|cap:&regex::Captures|{
  let h=cap[1].parse::<u32>().unwrap_or(0);
  format!("{} heure{}",if h==1{"une".to_owned()}else{integer(&h.to_string())},if h==1{""}else{"s"})
 }).into_owned();
 text=temp.replace_all(&text,|cap:&regex::Captures|{
   format!("{} degrés {}",number(&cap[1]),if cap[2].eq_ignore_ascii_case("c"){"Celsius"}else{"Fahrenheit"})
 }).into_owned();
 text=percent.replace_all(&text,|cap:&regex::Captures|format!("{} pour cent",number(&cap[1]))).into_owned();
 numbers.replace_all(&text,|cap:&regex::Captures|number(&cap["number"])).into_owned()
}
pub fn normalize(text:&str)->String{
 let urls=Regex::new(r"https?://[^\s<>]+|www\.[^\s<>]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}").unwrap();
 let mut out=String::new();let mut start=0;
 for matched in urls.find_iter(text){
  out.push_str(&part(&text[start..matched.start()]));
  out.push_str(matched.as_str());start=matched.end();
 }
 out.push_str(&part(&text[start..]));out
}
#[cfg(test)]
mod tests{
 use super::*;
 #[test]fn speaks_numbers(){
  assert_eq!(normalize("42"),"quarante-deux");
  assert_eq!(normalize("Il est 14h30."),"Il est quatorze heures trente.");
  assert_eq!(normalize("Il fait 20°C."),"Il fait vingt degrés Celsius.");
  assert_eq!(normalize("3,5"),"trois virgule cinq");
  assert_eq!(normalize("https://test.fr/v2"),"https://test.fr/v2");
 }
}
