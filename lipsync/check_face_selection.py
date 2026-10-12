#!/usr/bin/env python3
"""CPU reconstruction of official dominant-face selection on every 25 fps input frame."""
import argparse,json,os
from pathlib import Path
from runtime import ROOT,MODELS,configure_environment,sha256
p=argparse.ArgumentParser(description=__doc__)
p.add_argument("--video",required=True)
p.add_argument("--output",required=True)
a=p.parse_args()
configure_environment()
os.environ["CUDA_VISIBLE_DEVICES"]=""
import cv2,numpy as np,onnxruntime as ort
from insightface.model_zoo.model_zoo import ModelRouter
video=(ROOT/a.video).resolve();out=(ROOT/a.output).resolve()
if not video.is_relative_to(ROOT) or not out.is_relative_to(ROOT):raise RuntimeError("Project paths required")
weight=MODELS/"checkpoints/auxiliary/models/buffalo_l/det_10g.onnx"
expected=next(x for x in json.loads((MODELS/"manifest.json").read_text())["files"] if x["path"].endswith("/det_10g.onnx"))
assert sha256(weight)==expected["sha256"]
opts=ort.SessionOptions();opts.intra_op_num_threads=4;opts.inter_op_num_threads=1;opts.log_severity_level=3
d=ModelRouter(str(weight)).get_model(providers=["CPUExecutionProvider"],sess_options=opts)
d.prepare(ctx_id=-1,input_size=(512,512),det_thresh=.5)
assert d.session.get_providers()==["CPUExecutionProvider"]
cap=cv2.VideoCapture(str(video));fps=cap.get(cv2.CAP_PROP_FPS);w=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH));h=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT));rows=[]
while True:
 ok,frame=cap.read()
 if not ok:break
 boxes,_=d.detect(frame,max_num=0,metric="default")
 eligible=[]
 for b in boxes if boxes is not None else []:
  x1,y1,x2,y2=b[:4].astype(np.int64).tolist();fw,fh=x2-x1,y2-y1
  if fw>=50 and fh>=80 and .2<=fw/fh<=1.5 and float(b[4])>=.5:
   eligible.append({"bbox":[x1,y1,x2,y2],"score":float(b[4]),"area":fw*fh})
 eligible.sort(key=lambda x:x["area"],reverse=True)
 rows.append({"frame":len(rows),"seconds":len(rows)/fps,"detected":0 if boxes is None else len(boxes),"eligible":eligible,"chosen":eligible[0] if eligible else None})
cap.release()
centers=[[(r["chosen"]["bbox"][0]+r["chosen"]["bbox"][2])/2,(r["chosen"]["bbox"][1]+r["chosen"]["bbox"][3])/2] for r in rows if r["chosen"]]
jumps=[float(np.linalg.norm(np.array(centers[i])-centers[i-1])) for i in range(1,len(centers))]
report={"kind":"all_frame_cpu_reconstruction_of_official_largest_eligible_face_selection","video":str(video),"video_sha256":sha256(video),"fps":fps,"frames":len(rows),"geometry":[w,h],"model_sha256":expected["sha256"],"detector_input":[512,512],"provider":"CPUExecutionProvider","gpu_used":False,"missing_frames":[r["frame"] for r in rows if not r["chosen"]],"multiple_eligible_face_frames":[r["frame"] for r in rows if len(r["eligible"])>1],"chosen_center_x_range":[min(c[0] for c in centers),max(c[0] for c in centers)],"chosen_center_y_range":[min(c[1] for c in centers),max(c[1] for c in centers)],"max_adjacent_chosen_center_jump_pixels":max(jumps,default=0),"limitation":"Same model, 512 detector geometry and official eligibility/area rule on CPU; this is corroborating evidence, not a captured trace of CUDA inference decisions.","frames_detail":rows}
out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps({k:v for k,v in report.items() if k!="frames_detail"}),flush=True)
