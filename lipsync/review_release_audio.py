#!/usr/bin/env python3
"""Read-only final AAC versus WAV measurement; writes analysis reports only."""
import os
os.environ["OPENBLAS_NUM_THREADS"]="4"
os.environ["OMP_NUM_THREADS"]="4"
import argparse,hashlib,json,math,re,subprocess,time
from pathlib import Path
import av,numpy as np,imageio_ffmpeg

ROOT=Path(__file__).resolve().parents[1]
def digest(path):
 h=hashlib.sha256()
 with path.open("rb") as f:
  for block in iter(lambda:f.read(8*1024*1024),b""):h.update(block)
 return h.hexdigest()
def metadata(path):
 with av.open(str(path)) as c:
  if len(c.streams.audio)!=1:raise RuntimeError("Exactly one audio stream required")
  s=c.streams.audio[0]
  return {"codec":s.codec_context.name,"sample_rate":s.codec_context.sample_rate,"channels":s.codec_context.channels,"layout":s.codec_context.layout.name,"stream_start_seconds":float(s.start_time*s.time_base) if s.start_time is not None else None,"stream_duration_seconds":float(s.duration*s.time_base) if s.duration is not None else None,"container_duration_seconds":c.duration/1000000 if c.duration is not None else None}
def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument("--movie",required=True);p.add_argument("--wav",required=True);p.add_argument("--expected-sha256",required=True);p.add_argument("--report-prefix",required=True)
 a=p.parse_args()
 paths=[(ROOT/x).resolve() for x in [a.movie,a.wav,a.report_prefix]]
 if not all(x.is_relative_to(ROOT) for x in paths):raise RuntimeError("Project paths required")
 movie,wav,prefix=paths;prefix.parent.mkdir(parents=True,exist_ok=True)
 initial={str(x.relative_to(ROOT)):{"bytes":x.stat().st_size,"sha256":digest(x)} for x in [movie,wav]}
 if initial[str(movie.relative_to(ROOT))]["sha256"]!=a.expected_sha256:raise RuntimeError("Movie hash does not match authorized release")
 ff=imageio_ffmpeg.get_ffmpeg_exe()
 info={name:metadata(path) for name,path in [("movie",movie),("reference_wav",wav)]}
 if info["movie"]["codec"]!="aac" or any(v["sample_rate"]!=48000 or v["channels"]!=2 for v in info.values()):raise RuntimeError("Expected actual 48 kHz stereo AAC versus stereo WAV")
 def pcm(path):
  proc=subprocess.run([ff,"-hide_banner","-v","error","-nostdin","-i",str(path),"-map","0:a:0","-vn","-ac","2","-ar","48000","-f","f32le","-"],check=True,capture_output=True,timeout=180)
  if proc.stderr:raise RuntimeError("Decode emitted errors: "+proc.stderr.decode(errors="replace"))
  data=np.frombuffer(proc.stdout,dtype="<f4").reshape(-1,2).copy()
  if not np.isfinite(data).all():raise RuntimeError("Non-finite decoded audio")
  return data
 ref,encoded=pcm(wav),pcm(movie)
 expected=84*48000
 if len(ref)!=expected:raise RuntimeError("Reference WAV is not exactly 84 seconds")
 # Coarse normalized waveform comparison, followed by exact 48 kHz local lag search.
 n=min(len(ref),len(encoded))
 first=ref[:n].mean(axis=1,dtype=np.float64)[::3];second=encoded[:n].mean(axis=1,dtype=np.float64)[::3]
 first-=first.mean();second-=second.mean()
 size=1<<(len(first)+len(second)-2).bit_length()
 values=np.fft.irfft(np.fft.rfft(second,n=size)*np.conj(np.fft.rfft(first,n=size)),n=size)
 lags=np.arange(-16000,16001);coarse=int(lags[np.argmax(values[lags%size])])*3
 del values,first,second
 mono_ref=ref.mean(axis=1,dtype=np.float64);mono_enc=encoded.mean(axis=1,dtype=np.float64)
 def align(x,y,lag):
  if lag>=0:return x[:min(len(x),len(y)-lag)],y[lag:lag+min(len(x),len(y)-lag)]
  return x[-lag:-lag+min(len(x)+lag,len(y))],y[:min(len(x)+lag,len(y))]
 def correlation(x,y):
  x=x.astype(np.float64);y=y.astype(np.float64);x=x-x.mean();y=y-y.mean()
  den=float(np.sqrt(np.dot(x,x)*np.dot(y,y)))
  return float(np.dot(x,y)/den) if den else None
 lag_scores=[]
 for lag in range(coarse-4,coarse+5):
  x,y=align(mono_ref,mono_enc,lag);lag_scores.append((correlation(x,y),lag))
 best_score,best_lag=max(lag_scores)
 ref_aligned,enc_aligned=align(ref,encoded,best_lag)
 channels=[correlation(ref_aligned[:,i],enc_aligned[:,i]) for i in range(2)]
 windows=[]
 for i in range(14):
  lo=i*6*48000;hi=min((i+1)*6*48000,len(ref_aligned));x=ref_aligned[lo:hi].astype(np.float64);y=enc_aligned[lo:hi].astype(np.float64)
  windows.append({"shot":"S%02d"%(i+1),"start_seconds":i*6,"end_seconds":hi/48000,"correlation_left":correlation(x[:,0],y[:,0]),"correlation_right":correlation(x[:,1],y[:,1]),"reference_rms":float(np.sqrt(np.mean(x*x))),"encoded_rms":float(np.sqrt(np.mean(y*y)))})
 difference=enc_aligned.astype(np.float64)-ref_aligned
 reference_power=float(np.mean(ref_aligned.astype(np.float64)**2));error_power=float(np.mean(difference**2))
 def db(value):return float(20*np.log10(value)) if value>0 else None
 # ebur128 independently measures the encoded AAC after decoding; no output file is created.
 cmd=[ff,"-hide_banner","-nostdin","-i",str(movie),"-map","0:a:0","-vn","-af","ebur128=peak=true:framelog=verbose","-f","null","-"]
 measure=subprocess.run(cmd,capture_output=True,text=True,timeout=300)
 prefix.with_suffix(".log").write_text(measure.stderr,encoding="utf-8")
 if measure.returncode:raise RuntimeError("EBU R128 measurement failed")
 tail=measure.stderr[measure.stderr.rfind("Summary:"):]
 def extract(pattern):
  match=re.search(pattern,tail,re.S)
  if not match:raise RuntimeError("Missing ebur128 summary field")
  value=float(match.group(1))
  if not math.isfinite(value):raise RuntimeError("Non-finite ebur128 measure")
  return value
 integrated=extract(r"Integrated loudness:.*?I:\s*([-+0-9.]+)\s*LUFS")
 lra=extract(r"Loudness range:.*?LRA:\s*([-+0-9.]+)\s*LU")
 true_peak=extract(r"True peak:.*?Peak:\s*([-+0-9.]+)\s*dBFS")
 # The 0.1 dB ebur128 display can round a peak across the -1.50 boundary.
 ebur128={"integrated_lufs":integrated,"true_peak_dbtp":true_peak,"loudness_range_lu":lra,"display_precision_db":0.1}
 precise=subprocess.run([ff,"-hide_banner","-nostdin","-i",str(movie),"-map","0:a:0","-vn","-af","loudnorm=I=-16:TP=-1.5:LRA=9:print_format=json","-f","null","-"],capture_output=True,text=True,timeout=300)
 prefix.with_suffix(".loudnorm.log").write_text(precise.stderr,encoding="utf-8")
 if precise.returncode:raise RuntimeError("Loudnorm input measurement failed")
 matches=re.findall(r'\{\s*"input_i".*?\}',precise.stderr,re.S)
 if not matches:raise RuntimeError("Missing loudnorm input measurements")
 loudnorm_input=json.loads(matches[-1])
 integrated=float(loudnorm_input["input_i"]);true_peak=float(loudnorm_input["input_tp"]);lra=float(loudnorm_input["input_lra"])
 if not all(math.isfinite(v) for v in [integrated,true_peak,lra]):raise RuntimeError("Non-finite precise loudness")
 gates={"authorized_movie_sha256_matches":True,"codec_aac_48k_stereo":True,"reference_84_seconds":len(ref)==expected,"container_84_seconds":abs(info["movie"]["container_duration_seconds"]-84)<1e-6,"audio_stream_84_seconds":abs(info["movie"]["stream_duration_seconds"]-84)<1e-6,"decoded_aac_within_one_packet_padding":abs(len(encoded)-expected)<=1024,"alignment_within_2ms":abs(best_lag)<=96,"full_channel_correlation_at_least_0_98":min(channels)>=.98,"all_six_second_channel_correlations_at_least_0_95":min(min(w["correlation_left"],w["correlation_right"]) for w in windows)>=.95,"integrated_minus16_plusminus1_lufs":-17<=integrated<=-15,"aac_delivery_true_peak_at_most_minus1_dbtp":true_peak<=-1.0}
 unchanged=all(digest(x)==initial[str(x.relative_to(ROOT))]["sha256"] for x in [movie,wav]);gates["source_files_unchanged"]=unchanged
 report={"kind":"independent_encoded_aac_vs_final_mix_review","state":"completed","passed":all(gates.values()),"gates":gates,"files":initial,"metadata":info,"decoded_samples_per_channel":{"reference":len(ref),"encoded":len(encoded)},"decoded_seconds":{"reference":len(ref)/48000,"encoded":len(encoded)/48000},"decoded_padding_samples":len(encoded)-expected,"alignment":{"lag_samples_48000hz":best_lag,"lag_seconds":best_lag/48000,"sign_convention":"positive means encoded soundtrack is delayed relative to reference","search_window_seconds":1,"mono_correlation":best_score,"left_correlation":channels[0],"right_correlation":channels[1]},"per_six_second_window":windows,"difference":{"signal_to_error_ratio_db":float(10*np.log10(reference_power/error_power)) if error_power else None,"rms_error":float(np.sqrt(error_power)),"max_absolute_error":float(np.max(np.abs(difference)))},"decoded_sample_peak_dbfs_per_channel":[db(float(np.max(np.abs(encoded[:,i])))) for i in range(2)],"integrated_lufs":integrated,"true_peak_dbtp":true_peak,"loudness_range_lu":lra,"strict_mix_target_true_peak_at_most_minus1_5_dbtp":true_peak<=-1.5,"ebur128":ebur128,"loudnorm_input_crosscheck":loudnorm_input,"loudness_method":"FFmpeg ebur128 with true peak, cross-checked using higher precision loudnorm input measurements; decoded final AAC; media output discarded","ffmpeg":ff,"human_listened":False,"phoneme_level_certified":False,"limitations":["Waveform agreement verifies preservation/alignment against the supplied mix; it does not prove the original TTS pronunciation or acting quality.","No human listening or phoneme-level perceptual certification was performed.","AAC packet padding can extend raw decoded samples beyond the 84-second stream timeline.","EBU R128 display is rounded to 0.1 dB; threshold decisions use loudnorm input fields at 0.01 dB display precision. Delivery ceiling -1.0 dBTP and stricter mix ceiling -1.5 dBTP are reported separately."],"checked_at_unix":time.time()}
 prefix.with_suffix(".json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
 lines=["# 最终交付 AAC 独立音轨检查","",f"技术判定：{'通过' if report['passed'] else '未通过'}。输入为完整中文字幕交付片，文件 SHA 与任务给定一致；影片及 WAV 前后 SHA 均保持不变。","",f"- 容器与音轨时间线：{info['movie']['container_duration_seconds']:.3f} / {info['movie']['stream_duration_seconds']:.3f} 秒；AAC、48 kHz、双声道。",f"- 原始 WAV / 解码 AAC：{len(ref)/48000:.6f} / {len(encoded)/48000:.6f} 秒；原始解码尾部差 {len(encoded)-expected} 样本。",f"- 波形估计时差：{best_lag} 样本（{best_lag/48000:.6f} 秒）。左右声道相关性：{channels[0]:.9f} / {channels[1]:.9f}。",f"- 最低六秒分段声道相关性：{min(min(w['correlation_left'],w['correlation_right']) for w in windows):.9f}。",f"- 最终 AAC 整体响度：{integrated:.2f} LUFS；true peak：{true_peak:.2f} dBTP；LRA：{lra:.2f} LU（ebur128 独立测量并以 loudnorm 输入值提高显示精度；两份原始日志均保留）。",f"- AAC 交付峰值门槛为 ≤ −1.0 dBTP；另记录是否达到混音的严格 −1.5 dBTP：{true_peak<=-1.5}。","", "以上为独立解码与数值测量，没有人工试听或音素级认证。波形一致证明交付音轨保留并对齐指定混音，不证明原始台词发音与表演本身正确。","", "详细采样数、14 个六秒窗口、每声道指标、文件 SHA、门槛和原始 EBU R128 日志见同名前缀 JSON / LOG。"]
 prefix.with_suffix(".md").write_text("\n".join(lines)+"\n")
 print(json.dumps({k:report[k] for k in ["passed","gates","metadata","decoded_seconds","decoded_padding_samples","alignment","integrated_lufs","true_peak_dbtp","loudness_range_lu","strict_mix_target_true_peak_at_most_minus1_5_dbtp"]},ensure_ascii=False),flush=True)
 if not report["passed"]:raise SystemExit(2)
if __name__=="__main__":main()
